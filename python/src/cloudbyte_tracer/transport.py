from __future__ import annotations

import atexit
import collections
import contextvars
import datetime
import functools
import http.client
import json
import math
import numbers
import platform
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Optional

from ._version import SDK_VERSION

__all__ = ["Transport"]

_PREFIX = "cloudbyte_tracer:"
_PATH = "/v1/agent-spans"
_SENDER_THREAD_NAME = "cloudbyte-trace-sender"
_KINDS = ("agent", "llm", "tool", "retrieval", "custom")
_STATUSES = ("ok", "error", "unset")
_MAX_SPANS_PER_REQUEST = 500  # the server rejects a batch of more
_MAX_BODY_BYTES = 4_000_000  # the server refuses a request body of more than 5 MB; stay well under that
_MAX_FIELD_BYTES = 65536  # the server keeps at most 64 KB of an input or output and flags the rest as truncated
_CUT_FIELD_BYTES = 70000  # a longer one is sent cut to this: still over the server's limit, so it is still flagged
_MAX_NAME_CHARS = 200  # the server rejects a span name or model of more characters
_DEFAULT_BASE_URL = "https://api.cloudbyte.ai"
_DEFAULT_RETRY_AFTER = 5.0  # seconds to wait after a 429 whose Retry-After header is missing or not a whole number
_MAX_QUEUE_SPANS = 10_000  # bound memory while the sender is backing off
_MAX_QUEUE_BYTES = 8_000_000
_MAX_RETRY_AFTER = 60.0  # the longest a 429 makes the sender wait
# Tells the server which SDK and runtime sent a batch, e.g. "cloudbyte-tracer-python/0.1.0 (CPython 3.12.10)".
_USER_AGENT = "cloudbyte-tracer-python/%s (%s %s)" % (
    SDK_VERSION, platform.python_implementation(), platform.python_version())


def _log(message: str) -> None:
    try:
        stream = sys.stderr
        if stream is not None:
            print("%s %s" % (_PREFIX, message), file=stream)
    except Exception:
        pass


def _never_raises(method):
    """Turn any unexpected error in a public method into a stderr line, so it never reaches the caller."""
    @functools.wraps(method)
    def wrapper(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except Exception as exc:
            _log("%s failed: %s: %s" % (method.__name__, type(exc).__name__, "internal tracing error"))
    return wrapper


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _text(value) -> str:
    if isinstance(value, str):
        return value
    try:
        return str(value)
    except Exception:
        return "<unprintable %s>" % type(value).__name__


def _attribute_value(value):
    """The server accepts only strings, booleans and finite numbers as attribute values."""
    if isinstance(value, (str, bool, int)) or (isinstance(value, float) and math.isfinite(value)):
        return value
    return _text(value)


def _clean_attributes(attributes) -> dict:
    cleaned = {}
    if attributes is None:
        return cleaned
    try:
        for key, value in dict(attributes).items():
            cleaned[_text(key)] = _attribute_value(value)
    except Exception as exc:
        _log("ignoring attributes that are not a mapping: %s" % exc)
    return cleaned


def _clamp(text: str) -> str:
    """A span name or model cut to what the server accepts: it rejects a longer one, which would leave a run 'running'."""
    return text[:_MAX_NAME_CHARS]  # a Python string slice counts code points, as the server does


def _cut(text: str) -> str:
    """The first _CUT_FIELD_BYTES bytes of ``text`` in UTF-8, ending on a whole character."""
    # Every character is at least one byte, so the first _CUT_FIELD_BYTES characters hold all the bytes wanted.
    data = text[:_CUT_FIELD_BYTES].encode("utf-8", "replace")  # a lone surrogate becomes "?"
    return data[:_CUT_FIELD_BYTES].decode("utf-8", "ignore")  # "ignore" drops a character the cut left half of


def _fit(value):
    """An input or output as it is sent.

    The server stores at most 64 KB of one, so a value whose JSON text is longer is sent as a string of the first
    ~70,000 bytes of that text: the server cuts it to its limit and flags it truncated, and one huge value cannot
    push the request over the server's 5 MB limit, which would refuse the whole batch. Content JSON cannot hold
    (NaN, a circular structure, an odd key) is sent as its repr, cut the same way.
    """
    try:
        text = json.dumps(value, default=str, allow_nan=False, ensure_ascii=False, separators=(",", ":"))
        return value if len(text.encode("utf-8", "replace")) <= _MAX_FIELD_BYTES else _cut(text)
    except Exception:
        return _cut(repr(value))


def _encode(span: dict) -> Optional[str]:
    """JSON text for one span copy, with an oversized input or output cut (see ``_fit``). None if it cannot be encoded."""
    try:
        copy = dict(span)
        for field in ("input", "output"):
            if field in copy:
                copy[field] = _fit(copy[field])
        return json.dumps(copy, default=str, allow_nan=False)
    except Exception as exc:
        _log("dropping span %r: it cannot be encoded as JSON: %s" % (span.get("name"), exc))
        return None


def _server_message(payload: bytes) -> str:
    """The server's `error` text, or the start of the body when it is not the usual JSON."""
    try:
        message = json.loads(payload).get("error")
        if isinstance(message, str):
            return message
    except Exception:
        pass
    return payload.decode("utf-8", "replace")[:200]


def _retry_after(headers) -> float:
    """Seconds to wait after a 429: the Retry-After header (whole seconds) capped at _MAX_RETRY_AFTER, else the default."""
    try:
        seconds = int(headers.get("Retry-After", "").strip())
        if seconds >= 0:
            return float(min(seconds, _MAX_RETRY_AFTER))
    except Exception:
        pass
    return _DEFAULT_RETRY_AFTER


class Transport:
    """Creates spans and sends them to the server from a background thread.

    Root spans are queued on start and end; children are queued on end only. The sender thread posts the
    queue every ``flush_interval`` seconds, or as soon as ``max_batch`` spans (at most 500) are waiting; a batch
    is also cut at about 4 MB, and an input or output over 64 KB is sent cut (the server flags it truncated).
    A 5xx response or a connection error is retried after each of ``retry_delays`` seconds, then the batch
    is dropped; a 4xx response is not retried. Both are reported on stderr.
    A 429 (rate limit) waits for the Retry-After header (at most 60 seconds, 5 if it is missing or invalid)
    instead of the next retry delay, and uses one retry. A 403 (the agent is disabled, or Agent Observability is
    off for the organization) is reported once; the tracer then drops every further span.
    While the sender is blocked, the queue holds at most 10,000 span copies or 8 MB of encoded JSON;
    older copies are dropped first and one warning is printed. Tracing never waits for queue space.
    """

    def __init__(self, api_key: str, base_url: str = _DEFAULT_BASE_URL, *, capture_content: bool = True,
                 flush_interval: float = 1.0, max_batch: int = 100, timeout: float = 5.0,
                 retry_delays: tuple = (0.5, 1.0, 2.0)) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("api_key must be a non-empty string")
        if not isinstance(base_url, str):
            raise ValueError("base_url must be an http:// or https:// URL")
        parts = urllib.parse.urlsplit(base_url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ValueError("base_url must be an http:// or https:// URL, got %r" % base_url)

        if parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError("base_url cannot contain credentials, query or fragment")
        self._api_key = api_key.strip()
        self._base_url = base_url.rstrip("/")
        self._endpoint = self._base_url + _PATH
        self._capture_content = bool(capture_content)
        self._flush_interval = max(0.0, float(flush_interval))
        self._max_batch = max(1, min(int(max_batch), _MAX_SPANS_PER_REQUEST))
        self._timeout = float(timeout)
        self._retry_delays = tuple(max(0.0, float(delay)) for delay in retry_delays)

        self._current = contextvars.ContextVar("cloudbyte_current_span_%x" % id(self), default=None)
        self._queue: collections.deque = collections.deque()  # (json text, span name, span id)
        self._queue_bytes = 0
        self._overflow_reported = False
        self._cond = threading.Condition()  # guards everything below
        self._enqueued = 0  # span copies ever queued
        self._processed = 0  # span copies the sender has finished with, whether sent or dropped
        self._pending = set()  # tickets still queued or in flight; bounded by queue + one batch
        self._flush_requested = False
        self._stop = threading.Event()
        self._disabled = False  # set by the server answering 403; read without the lock, it only ever goes False to True

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        self._opener = urllib.request.build_opener(NoRedirect)
        self._thread = threading.Thread(target=self._run, name=_SENDER_THREAD_NAME, daemon=True)
        self._thread.start()
        atexit.register(self.shutdown)

    @_never_raises
    def flush(self, timeout: float = 5.0) -> None:
        """Block until every span queued so far has been sent or dropped, for at most ``timeout`` seconds."""
        deadline = time.monotonic() + float(timeout)
        with self._cond:
            target = self._enqueued
            if not any(ticket <= target for ticket in self._pending) or not self._thread.is_alive():
                return
            self._flush_requested = True
            self._cond.notify_all()
            while any(ticket <= target for ticket in self._pending):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _log("flush timed out with %d span(s) still unsent" % sum(ticket <= target for ticket in self._pending))
                    return
                self._cond.wait(remaining)

    @_never_raises
    def shutdown(self) -> None:
        """Flush, then stop the sender thread. Spans created afterwards are not sent. Runs at process exit."""
        if self._stop.is_set():
            return
        self.flush()
        with self._cond:
            self._stop.set()
            self._cond.notify_all()
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout=2.0)
        atexit.unregister(self.shutdown)

    def _enqueue(self, span: dict) -> None:
        if self._stop.is_set() or self._disabled:
            return
        encoded = self._encode(span)  # now, so that later changes to a caller's input object are not sent
        if encoded is None:
            return
        with self._cond:
            if self._stop.is_set() or self._disabled:
                return
            if len(encoded) + 12 > _MAX_BODY_BYTES:
                if not self._overflow_reported:
                    self._overflow_reported = True
                    _log("copy exceeds request byte limit; dropping it")
                return
            if len(encoded) > _MAX_QUEUE_BYTES:
                # Larger than the whole queue could ever hold: drop it alone. Evicting queued spans for it would lose them for nothing.
                if not self._overflow_reported:
                    self._overflow_reported = True
                    _log("span %r is larger than the queue limit; dropping it" % span["name"])
                return
            dropped = 0
            while self._queue and (len(self._queue) >= _MAX_QUEUE_SPANS or self._queue_bytes + len(encoded) > _MAX_QUEUE_BYTES):
                item = self._queue.popleft()
                self._queue_bytes -= len(item[0])
                self._pending.discard(item[3])
                self._processed += 1
                dropped += 1
            if dropped and not self._overflow_reported:
                self._overflow_reported = True
                _log("queue full; dropping oldest spans to keep tracing memory bounded")
            if dropped:
                self._cond.notify_all()
            self._queue.append((encoded, span["name"], span["span_id"], self._enqueued + 1))
            self._pending.add(self._enqueued + 1)
            self._queue_bytes += len(encoded)
            self._enqueued += 1
            if len(self._queue) == 1 or len(self._queue) >= self._max_batch:
                self._cond.notify_all()  # wake an idle sender, or one holding a batch open that is now full

    def _run(self) -> None:
        while True:
            with self._cond:
                if self._stop.is_set():
                    self._processed += len(self._queue)
                    for item in self._queue:
                        self._pending.discard(item[3])
                    self._queue.clear()
                    self._queue_bytes = 0
                    self._cond.notify_all()
                    return
                while not self._queue:
                    if self._stop.is_set():
                        return
                    self._flush_requested = False
                    self._cond.wait()
                # Hold the batch open for flush_interval, unless it fills up or a flush or shutdown asks for it now.
                deadline = time.monotonic() + self._flush_interval
                while len(self._queue) < self._max_batch and not self._flush_requested and not self._stop.is_set():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    self._cond.wait(remaining)
                batch = self._take_batch()
            try:
                self._send(batch)
            except Exception as exc:
                _log("dropped %d span(s): %s: %s" % (len(batch), type(exc).__name__, "internal tracing error"))
            finally:
                with self._cond:
                    self._processed += len(batch)
                    for item in batch:
                        self._pending.discard(item[3])
                    self._cond.notify_all()

    def _take_batch(self) -> list:
        """Pops the next batch off the queue (call with the lock held): at most ``max_batch`` spans, and no more
        than fit in a request body of _MAX_BODY_BYTES. A span that is too big for that alone is still sent, alone."""
        batch = []
        size = len('{"spans":[]}')
        while self._queue and len(batch) < self._max_batch:
            size += len(self._queue[0][0]) + 1  # the JSON text is ASCII, so its length is its bytes; 1 for the comma
            if batch and size > _MAX_BODY_BYTES:
                break
            item = self._queue.popleft()
            self._queue_bytes -= len(item[0])
            batch.append(item)
        return batch

    def _send(self, batch: list) -> None:
        body = ('{"spans":[' + ",".join(item[0] for item in batch) + "]}").encode("utf-8")
        attempt = 0
        while True:
            finished, problem, wait = self._post_once(body, batch)
            if finished:
                return
            if wait is None and attempt < len(self._retry_delays):
                wait = self._retry_delays[attempt]
            if attempt >= len(self._retry_delays) or self._stop.wait(wait):
                break  # out of retries, or the tracer is shutting down
            attempt += 1
        _log("dropped %d span(s) after %d attempt(s): %s" % (len(batch), attempt + 1, problem))

    def _post_once(self, body: bytes, batch: list):
        """One POST. Returns (True, "", None) when the batch is settled, (False, why, wait) when it is worth retrying:
        ``wait`` is the seconds the server asked for (a 429), or None to use the next retry delay."""
        request = urllib.request.Request(self._endpoint, data=body, method="POST", headers={
            "Authorization": "Bearer " + self._api_key, "Content-Type": "application/json",
            "User-Agent": _USER_AGENT})
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                self._log_rejections(response.read(65536), batch)
                return True, "", None
        except urllib.error.HTTPError as err:  # any status of 300 or more
            try:
                err.read(65536)
                message = "request refused"
            except OSError:
                message = ""
            finally:
                err.close()
            if err.code == 429:
                return False, "HTTP 429: %s" % message, _retry_after(err.headers)
            if err.code in (401, 403):
                self._disable("HTTP %d: access denied" % err.code)
                return True, "", None
            if err.code >= 500:
                return False, "HTTP %d: %s" % (err.code, message), None
            _log("server refused %d span(s) (HTTP %d, not retried): %s" % (len(batch), err.code, message))
            return True, "", None
        except (urllib.error.URLError, OSError, http.client.HTTPException) as err:
            return False, "network error", None

    def _disable(self, message: str) -> None:
        """The server said 403: report it once, drop what is queued, and drop every span from now on."""
        with self._cond:
            if self._disabled:
                return
            self._disabled = True
            self._processed += len(self._queue)  # dropped, so a flush() waiting on them is released
            for item in self._queue:
                self._pending.discard(item[3])
            self._queue.clear()
            self._queue_bytes = 0
            self._cond.notify_all()
        _log("%s; not sending further spans" % message)

    def _log_rejections(self, payload: bytes, batch: list) -> None:
        if not self._debug:
            return
        try:
            rejected = json.loads(payload).get("rejected") or []
            for item in rejected:
                _log("server rejected span at batch index %s" % item.get("index"))
        except Exception:
            pass

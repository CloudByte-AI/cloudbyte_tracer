from __future__ import annotations
import os, functools, inspect
from typing import Optional
from .transport import Transport, _log, _never_raises, _text, _clamp, _clean_attributes, _attribute_value, _now, _KINDS, _STATUSES
import uuid, numbers
from .limits import encode
class Span:
    """One timed step of an agent run. Created by ``Tracer.span()``; use it as a context manager.

    Leaving the ``with`` block normally gives the span status ``ok`` unless ``set_status()`` chose another;
    an exception gives it status ``error`` (with the exception's type and message) and is re-raised unchanged.
    """

    trace_id: str
    span_id: str
    parent_span_id: Optional[str]

    def __init__(self, tracer: "Tracer", name, kind, parent: Optional["Span"], input, model, attributes) -> None:
        self._tracer = tracer
        self._parent = parent
        self.trace_id = parent.trace_id if parent is not None else uuid.uuid4().hex
        self.span_id = uuid.uuid4().hex[:16]
        self.parent_span_id = parent.span_id if parent is not None else None
        self._name = _clamp(_text(name)) or "span"
        if kind not in _KINDS:
            _log("unknown span kind %r for span %r; using 'custom'" % (kind, self._name))
            kind = "custom"
        self._kind = kind
        self._input = input if tracer._capture_content else None
        self._output = None
        self._model = None if model is None else _clamp(_text(model))
        self._attributes = _clean_attributes(attributes)
        self._usage: dict = {}
        self._status: Optional[str] = None
        self._error: Optional[dict] = None
        self._started_at: Optional[str] = None
        self._token = None

    def __enter__(self) -> "Span":
        try:
            self._started_at = _now()
            self._token = self._tracer._current.set(self)
            if self._tracer._bridge is not None:
                self._otel_span, self._otel_context = self._tracer._bridge.start(self)
                ctx = self._otel_span.get_span_context()
                self.trace_id, self.span_id = format(ctx.trace_id, "032x"), format(ctx.span_id, "016x")
                parent = self._otel_span.parent
                self.parent_span_id = format(parent.span_id, "016x") if parent else None
            elif self.parent_span_id is None:
                self._tracer._enqueue(self._start_copy())
        except Exception as exc:
            _log("could not start span %r: %s" % (self._name, exc))
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        try:
            if exc_type is not None:
                self._status = "error"
                self._error = {"type": exc_type.__name__, "message": _text(exc)}
            self._restore_current()
            if getattr(self, "_otel_span", None) is not None:
                self._tracer._bridge.end(self, exc)
            else:
                self._tracer._enqueue(self._end_copy())
        except Exception as err:
            _log("could not finish span %r: %s" % (self._name, err))
        return False  # never swallow the traced code's exception

    @_never_raises
    def set_output(self, value) -> None:
        if self._tracer._capture_content:
            self._output = value

    @_never_raises
    def set_usage(self, *, input_tokens=None, output_tokens=None, cache_read_tokens=None, cache_write_tokens=None) -> None:
        given = {"input_tokens": input_tokens, "output_tokens": output_tokens,
                 "cache_read_tokens": cache_read_tokens, "cache_write_tokens": cache_write_tokens}
        for key, value in given.items():
            if value is None:
                continue
            if isinstance(value, numbers.Integral) and not isinstance(value, bool) and value >= 0:
                self._usage[key] = min(int(value), 2147483647)
            else:
                _log("ignoring %s=%r on span %r: it must be a non-negative integer" % (key, value, self._name))

    @_never_raises
    def set_attribute(self, key: str, value) -> None:
        self._attributes[_text(key)] = _attribute_value(value)

    @_never_raises
    def set_status(self, status: str, *, error_type=None, error_message=None) -> None:
        if status not in _STATUSES:
            _log("ignoring status %r on span %r; use 'ok', 'error' or 'unset'" % (status, self._name))
            return
        self._status = status
        if status != "error":
            self._error = None
        elif error_type is not None or error_message is not None:
            self._error = {"type": "Error" if error_type is None else _text(error_type),
                           "message": "" if error_message is None else _text(error_message)}

    @_never_raises
    def record_error(self, exc) -> None:
        self._status = "error"
        self._error = {"type": type(exc).__name__, "message": _text(exc)}

    def _restore_current(self) -> None:
        token, self._token = self._token, None
        if token is None:
            return
        try:
            self._tracer._current.reset(token)
        except (ValueError, RuntimeError):  # the block exits in a different context than it entered
            self._tracer._current.set(self._parent)

    def _identity(self) -> dict:
        return {"trace_id": self.trace_id, "span_id": self.span_id, "parent_span_id": self.parent_span_id,
                "kind": self._kind, "name": self._name, "started_at": self._started_at}

    def _start_copy(self) -> dict:
        # The server merges re-sent copies field by field and keeps a stored value when a field is absent, so
        # this copy must not carry status, output, usage or error: if it arrived late it would overwrite them.
        copy = self._identity()
        copy["ended_at"] = None
        if self._input is not None:
            copy["input"] = self._input
        if self._model is not None:
            copy["model"] = self._model
        if self._attributes:
            copy["attributes"] = self._attributes
        return copy

    def _end_copy(self) -> dict:
        copy = self._identity()
        copy["ended_at"] = _now()
        copy["status"] = self._status or "ok"
        if self._error is not None:
            copy["error"] = self._error
        if self._model is not None:
            copy["model"] = self._model
        if self._usage:
            copy["usage"] = self._usage
        if self._input is not None:
            copy["input"] = self._input
        if self._output is not None:
            copy["output"] = self._output
        if self._attributes:
            copy["attributes"] = self._attributes
        return copy



class Tracer(Transport):
    """Manual tracer; optional OpenInference integrations use the same bounded sender."""
    def __init__(self, api_key=None, base_url=None, *, capture_content=None, redact=None,
                 service_name=None, auto_capture=None, debug=False, tracer_provider=None, **options):
        self._debug = debug
        self._redact = redact
        self._bridge = None
        self._service_name = service_name
        key = api_key if api_key is not None else os.getenv("CLOUDBYTE_AGENT_KEY")
        disabled = os.getenv("CLOUDBYTE_DISABLED", "").lower() in ("1", "true", "yes", "on")
        missing = not isinstance(key, str) or not key.strip()
        if capture_content is None:
            capture_content = os.getenv("CLOUDBYTE_CAPTURE_CONTENT", "true").lower() not in ("0", "false", "no", "off")
        try:
            super().__init__(key if not missing else "disabled", base_url or os.getenv("CLOUDBYTE_BASE_URL", "https://api.cloudbyte.ai"), capture_content=capture_content, **options)
        except Exception:
            super().__init__("disabled", "https://api.cloudbyte.ai", capture_content=False)
            disabled = True
        if missing or disabled:
            self._disabled = True
            _log("tracing disabled (missing key or disabled configuration)")
        elif auto_capture or tracer_provider is not None:
            try:
                from .otel.instrument import Bridge
                self._bridge = Bridge(self, auto_capture, tracer_provider)
            except Exception:
                _log("auto-capture unavailable; check optional dependencies")

    def _encode(self, span):
        return encode(span, self._capture_content, self._redact, self._service_name)

    def span(self, name, kind="custom", *, input=None, model=None, attributes=None, parent=None):
        explicit_parent = parent if isinstance(parent, Span) else None
        parent = explicit_parent or self._current.get()
        span = Span(self, name, kind, parent, input, model, attributes)
        span._explicit_parent = explicit_parent
        return span

    def trace(self, fn=None, *, name=None, kind="custom"):
        def decorate(func):
            if inspect.iscoroutinefunction(func):
                @functools.wraps(func)
                async def wrapped(*args, **kwargs):
                    with self.span(name or func.__name__, kind=kind):
                        return await func(*args, **kwargs)
            else:
                @functools.wraps(func)
                def wrapped(*args, **kwargs):
                    with self.span(name or func.__name__, kind=kind):
                        return func(*args, **kwargs)
            return wrapped
        return decorate(fn) if fn is not None else decorate

    def shutdown(self):
        if self._bridge is not None:
            try:
                self._bridge.shutdown()
            except Exception:
                _log("auto-capture shutdown failed")
            self._bridge = None
        super().shutdown()

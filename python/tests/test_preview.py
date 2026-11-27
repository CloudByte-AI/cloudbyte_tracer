"""Tests for cloudbyte_tracer.py. They run against a fake local HTTP server, so the POC server is not needed.

Run from this folder:  python -m unittest test_cloudbyte_tracer -v
or from the project root:  python -m unittest examples/test_cloudbyte_tracer.py
"""
import asyncio
import contextlib
import io
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # so that cloudbyte_tracer imports from the project root too
from cloudbyte_tracer import Tracer  # noqa: E402
from unittest.mock import patch
import cloudbyte_tracer.transport as cloudbyte_tracer

ENDPOINT = "/v1/agent-spans"
TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


def strict_json(text):
    """Parse like the real server does: NaN and Infinity are not JSON."""
    def refuse(constant):
        raise ValueError("invalid JSON constant " + constant)
    return json.loads(text, parse_constant=refuse)


class FakeServer:
    """Stand-in for POST /v1/agent-spans: records (path, headers, json body) and answers with a chosen status."""

    def __init__(self):
        self.requests = []
        self.sizes = []  # byte length of each request body, parallel to `requests`
        self.status = 200
        self.reply = None  # JSON to answer with; by default {"accepted": n, "rejected": []} or {"error": "..."}
        self.script = []  # (status, JSON, headers) answers given to the next requests, in order, before the defaults above
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                try:
                    body = strict_json(raw)
                except ValueError:
                    self.respond(400, {"error": "Body is not JSON"})
                    return
                fake.requests.append((self.path, self.headers, body))
                fake.sizes.append(len(raw))
                if fake.script:
                    status, payload, headers = fake.script.pop(0)
                    self.respond(status, payload, headers)
                elif fake.reply is not None:
                    self.respond(fake.status, fake.reply)
                elif fake.status == 200:
                    self.respond(200, {"accepted": len(body["spans"]), "rejected": []})
                else:
                    self.respond(fake.status, {"error": "fake failure"})

            def respond(self, status, payload, headers=None):
                data = json.dumps(payload).encode()
                self.send_response(status)
                for name, value in (headers or {}).items():
                    self.send_header(name, value)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return "http://127.0.0.1:%d" % self.httpd.server_address[1]

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()

    def spans(self):
        return [span for _, _, body in self.requests for span in body["spans"]]

    def copies(self, name):
        return [span for span in self.spans() if span["name"] == name]

    def final(self, name):
        done = [span for span in self.copies(name) if span["ended_at"] is not None]
        assert len(done) == 1, "expected one finished copy of %r, got %d" % (name, len(done))
        return done[0]

    def started(self, name):
        begun = [span for span in self.copies(name) if span["ended_at"] is None]
        assert len(begun) == 1, "expected one start copy of %r, got %d" % (name, len(begun))
        return begun[0]


def queued_copies(tracer):
    """The copies waiting to be sent, in send order: root end copies first, then the rest in arrival order."""
    return list(tracer._priority) + list(tracer._queue)


def wait_for(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return condition()


class TracerTestCase(unittest.TestCase):
    allow_stderr = False

    def setUp(self):
        self.server = FakeServer()
        self.addCleanup(self.server.stop)
        self.stderr = io.StringIO()
        redirect = contextlib.redirect_stderr(self.stderr)
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)

    def tearDown(self):
        if not self.allow_stderr:
            self.assertEqual(self.stderr.getvalue(), "")

    def tracer(self, **options):
        settings = dict(base_url=self.server.url, flush_interval=0.05, retry_delays=(0, 0, 0))
        settings.update(options)
        tracer = Tracer(settings.pop("api_key", "cbagt_TTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTT"), **settings)
        self.addCleanup(tracer.shutdown)
        return tracer


class TracingTests(TracerTestCase):
    def test_nested_spans_share_trace_and_link_parents(self):
        tracer = self.tracer()
        with tracer.span("outer", kind="agent") as outer:
            with tracer.span("inner", kind="tool") as inner:
                pass
        tracer.flush()

        self.assertIsNone(outer.parent_span_id)
        self.assertEqual(inner.parent_span_id, outer.span_id)
        self.assertEqual(inner.trace_id, outer.trace_id)
        self.assertRegex(outer.trace_id, r"^[0-9a-f]{32}$")
        self.assertRegex(outer.span_id, r"^[0-9a-f]{16}$")
        self.assertNotEqual(outer.span_id, inner.span_id)

        final_outer, final_inner = self.server.final("outer"), self.server.final("inner")
        self.assertEqual(final_inner["parent_span_id"], outer.span_id)
        self.assertIsNone(final_outer["parent_span_id"])
        for final in (final_outer, final_inner):
            self.assertEqual(final["trace_id"], outer.trace_id)
            self.assertEqual(final["status"], "ok")
            self.assertRegex(final["started_at"], TIMESTAMP)
            self.assertRegex(final["ended_at"], TIMESTAMP)
            self.assertGreaterEqual(final["ended_at"], final["started_at"])
        self.assertEqual(final_outer["kind"], "agent")
        self.assertEqual(final_inner["kind"], "tool")

    def test_top_level_spans_start_new_traces(self):
        tracer = self.tracer()
        with tracer.span("first") as first:
            pass
        with tracer.span("second") as second:
            pass
        self.assertNotEqual(first.trace_id, second.trace_id)
        self.assertIsNone(second.parent_span_id)

    def test_exception_marks_error_and_reraises(self):
        tracer = self.tracer()
        raised = ValueError("bad")
        with self.assertRaises(ValueError) as caught:
            with tracer.span("boom"):
                raise raised
        self.assertIs(caught.exception, raised)
        tracer.flush()

        final = self.server.final("boom")
        self.assertEqual(final["status"], "error")
        self.assertEqual(final["error"], {"type": "ValueError", "message": "bad"})

    def test_set_status_overrides_ok(self):
        tracer = self.tracer()
        with tracer.span("checkout") as s:
            s.set_status("error", error_message="no stock")
        tracer.flush()

        final = self.server.final("checkout")
        self.assertEqual(final["status"], "error")
        self.assertEqual(final["error"], {"type": "Error", "message": "no stock"})

    def test_invalid_status_is_ignored_with_a_warning(self):
        self.allow_stderr = True
        tracer = self.tracer()
        with tracer.span("odd") as s:
            s.set_status("great")
        tracer.flush()

        self.assertEqual(self.server.final("odd")["status"], "ok")
        self.assertIn("cloudbyte_tracer:", self.stderr.getvalue())

    def test_asyncio_task_inherits_parent(self):
        tracer = self.tracer()

        async def main():
            with tracer.span("outer", kind="agent") as outer:
                async def child():
                    with tracer.span("inner") as inner:
                        return inner
                return outer, await asyncio.create_task(child())

        outer, inner = asyncio.run(main())
        self.assertEqual(inner.parent_span_id, outer.span_id)
        self.assertEqual(inner.trace_id, outer.trace_id)

    def test_thread_with_explicit_parent(self):
        tracer = self.tracer()
        made = {}

        def work(label, **options):
            with tracer.span(label, **options) as s:
                made[label] = s

        with tracer.span("outer", kind="agent") as outer:
            linked = threading.Thread(target=work, args=("linked",), kwargs={"parent": outer})
            loose = threading.Thread(target=work, args=("loose",))
            for thread in (linked, loose):
                thread.start()
                thread.join()

        self.assertEqual(made["linked"].parent_span_id, outer.span_id)
        self.assertEqual(made["linked"].trace_id, outer.trace_id)
        # A plain thread does not inherit the context: it starts its own trace.
        self.assertIsNone(made["loose"].parent_span_id)
        self.assertNotEqual(made["loose"].trace_id, outer.trace_id)

    def test_invalid_kind_becomes_custom_with_a_warning(self):
        self.allow_stderr = True
        tracer = self.tracer()
        with tracer.span("odd", kind="database"):
            pass
        tracer.flush()

        self.assertEqual(self.server.final("odd")["kind"], "custom")
        self.assertIn("cloudbyte_tracer:", self.stderr.getvalue())
        self.assertIn("database", self.stderr.getvalue())

    def test_default_kind_is_custom(self):
        tracer = self.tracer()
        with tracer.span("plain"):
            pass
        tracer.flush()
        self.assertEqual(self.server.final("plain")["kind"], "custom")


class ContractTests(TracerTestCase):
    def test_start_copy_carries_only_start_fields(self):
        tracer = self.tracer()
        with tracer.span("bare"):
            pass
        with tracer.span("full", kind="llm", input={"q": 1}, model="m", attributes={"a": 1}) as s:
            s.set_output("out")
            s.set_usage(input_tokens=1, output_tokens=2)
            s.set_status("error", error_type="Oops", error_message="no")
        tracer.flush()

        self.assertEqual(set(self.server.started("bare")), {
            "trace_id", "span_id", "parent_span_id", "kind", "name", "started_at", "ended_at"})
        self.assertEqual(set(self.server.started("full")), {
            "trace_id", "span_id", "parent_span_id", "kind", "name", "started_at", "ended_at",
            "input", "model", "attributes"})
        self.assertIsNone(self.server.started("full")["ended_at"])

        final = self.server.final("full")
        self.assertEqual(final["output"], "out")
        self.assertEqual(final["input"], {"q": 1})
        self.assertEqual(final["model"], "m")
        self.assertEqual(final["attributes"], {"a": 1})
        self.assertEqual(final["usage"], {"input_tokens": 1, "output_tokens": 2})
        self.assertEqual(final["error"], {"type": "Oops", "message": "no"})
        self.assertNotIn("error", self.server.final("bare"))
        self.assertNotIn("usage", self.server.final("bare"))

    def test_capture_content_false_omits_input_output(self):
        tracer = self.tracer(capture_content=False)
        with tracer.span("llm", kind="llm", input=[{"role": "user", "content": "secret"}],
                         model="claude-sonnet-4-5", attributes={"gen_ai.system": "anthropic"}) as s:
            s.set_output("also secret")
            s.set_usage(input_tokens=5, output_tokens=6)
        tracer.flush()

        spans = self.server.spans()
        self.assertEqual(len(spans), 2)
        for span in spans:
            self.assertNotIn("input", span)
            self.assertNotIn("output", span)
        final = self.server.final("llm")
        self.assertEqual(final["model"], "claude-sonnet-4-5")
        self.assertEqual(final["usage"], {"input_tokens": 5, "output_tokens": 6})
        self.assertEqual(final["attributes"], {"gen_ai.system": "anthropic"})

    def test_model_and_usage_sent(self):
        tracer = self.tracer()
        with tracer.span("draft_reply", kind="llm", model="claude-sonnet-4-5") as s:
            s.set_usage(input_tokens=1200, output_tokens=300)
        tracer.flush()

        final = self.server.final("draft_reply")
        self.assertEqual(final["model"], "claude-sonnet-4-5")
        self.assertEqual(final["usage"], {"input_tokens": 1200, "output_tokens": 300})

    def test_set_usage_merges_and_skips_bad_values(self):
        self.allow_stderr = True
        tracer = self.tracer()
        with tracer.span("llm", kind="llm") as s:
            s.set_usage(input_tokens=10)
            s.set_usage(output_tokens=-1, cache_read_tokens=4, cache_write_tokens="lots")
        tracer.flush()

        self.assertEqual(self.server.final("llm")["usage"], {"input_tokens": 10, "cache_read_tokens": 4})
        self.assertIn("cloudbyte_tracer:", self.stderr.getvalue())

    def test_attributes_are_flat_scalars(self):
        tracer = self.tracer()
        with tracer.span("t", attributes={"n": 1, "f": 1.5, "s": "x", "b": True, "list": [1, 2],
                                          "dict": {"k": "v"}, "nan": float("nan"), "none": None}) as s:
            s.set_attribute("later", ("a", "b"))
            s.set_attribute("flag", False)
        tracer.flush()

        attributes = self.server.final("t")["attributes"]
        self.assertEqual(attributes, {"n": 1, "f": 1.5, "s": "x", "b": True, "list": "[1, 2]",
                                      "dict": "{'k': 'v'}", "nan": "nan", "none": "None",
                                      "later": "('a', 'b')", "flag": False})

    def test_unserialisable_content_is_still_sent(self):
        tracer = self.tracer()
        circular = {}
        circular["self"] = circular
        with tracer.span("odd", input=circular) as s:
            s.set_output({"score": float("nan"), "when": object})
        tracer.flush()

        final = self.server.final("odd")
        self.assertIsInstance(final["input"], str)
        self.assertIsInstance(final["output"], str)

    def test_authorization_header(self):
        tracer = self.tracer()
        with tracer.span("t"):
            pass
        tracer.flush()

        path, headers, body = self.server.requests[0]
        self.assertEqual(path, ENDPOINT)
        self.assertEqual(headers["Authorization"], "Bearer cbagt_TTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTT")
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(list(body), ["spans"])

    def test_user_agent_names_sdk_and_runtime(self):
        import platform
        from cloudbyte_tracer import __version__

        tracer = self.tracer()
        with tracer.span("t"):
            pass
        tracer.flush()

        _path, headers, _body = self.server.requests[0]
        self.assertEqual(headers["User-Agent"], "cloudbyte-tracer-python/%s (%s %s)" % (
            __version__, platform.python_implementation(), platform.python_version()))
        self.assertNotEqual(__version__, "0+unknown")  # the test install carries package metadata


class SendingTests(TracerTestCase):
    def test_5xx_retried_then_dropped(self):
        self.allow_stderr = True
        self.server.status = 500
        # A long interval keeps the start and end copies in one batch until flush() sends them together.
        tracer = self.tracer(flush_interval=30)
        with tracer.span("t"):
            pass
        tracer.flush()

        self.assertEqual(len(self.server.requests), 4)  # one try plus three retries
        self.assertIn("cloudbyte_tracer:", self.stderr.getvalue())

    def test_4xx_not_retried(self):
        self.allow_stderr = True
        self.server.status = 400
        self.server.reply = {"error": "Body must be { spans }"}
        tracer = self.tracer(flush_interval=30)
        with tracer.span("t"):
            pass
        tracer.flush()

        self.assertEqual(len(self.server.requests), 1)
        self.assertIn("cloudbyte_tracer:", self.stderr.getvalue())
        self.assertIn("HTTP 400", self.stderr.getvalue())

    def test_rejected_spans_are_logged_and_not_retried(self):
        self.allow_stderr = True
        self.server.reply = {"accepted": 1, "rejected": [{"index": 0, "error": "name must be 1 to 200 characters"}]}
        tracer = self.tracer(flush_interval=30, debug=True)
        with tracer.span("t"):
            pass
        tracer.flush()

        self.assertEqual(len(self.server.requests), 1)
        self.assertIn("server rejected span at batch index 0", self.stderr.getvalue())

    def test_429_waits_retry_after_then_retries(self):
        self.server.script = [(429, {"error": "Rate limit exceeded"}, {"Retry-After": "0"})]
        tracer = self.tracer(flush_interval=30, retry_delays=(0,))  # one retry in the budget: the 429 uses it up
        with tracer.span("t"):
            pass
        tracer.flush()

        self.assertEqual(len(self.server.requests), 2)
        self.assertEqual([span["name"] for span in self.server.requests[1][2]["spans"]], ["t", "t"])  # nothing dropped

    def test_429_counts_against_the_retry_budget(self):
        self.allow_stderr = True
        self.server.script = [(429, {"error": "Rate limit exceeded"}, {"Retry-After": "0"})] * 2
        tracer = self.tracer(flush_interval=30, retry_delays=(0,))
        with tracer.span("t"):
            pass
        tracer.flush()

        self.assertEqual(len(self.server.requests), 2)  # one try plus the one retry
        self.assertIn("dropped 2 span(s) after 2 attempt(s)", self.stderr.getvalue())
        self.assertIn("HTTP 429", self.stderr.getvalue())

    def test_retry_after_wait_is_capped_and_defaults(self):
        waits = []
        for header, expected in [("0", 0), ("3", 3), ("9999", 60), (None, 5), ("soon", 5), ("-4", 5)]:
            with self.subTest(header=header):
                self.server.script = [(429, {"error": "Rate limit exceeded"}, {} if header is None else {"Retry-After": header})]
                tracer = self.tracer(flush_interval=30, retry_delays=(0,))
                tracer._stop.wait = lambda seconds: waits.append(seconds) or False  # record the wait instead of sleeping
                with tracer.span("t"):
                    pass
                tracer.flush()
                self.assertEqual(waits.pop(), expected)

    def test_403_stops_sending_once(self):
        self.allow_stderr = True
        self.server.script = [(403, {"error": "Agent is disabled"}, {})]
        tracer = self.tracer(flush_interval=30)
        with tracer.span("first"):
            pass
        tracer.flush()
        with tracer.span("second") as later:
            later.set_output("still works")
        tracer.flush()

        self.assertEqual(len(self.server.requests), 1)  # "second" is never POSTed
        self.assertEqual(self.stderr.getvalue(), "cloudbyte_tracer: HTTP 403: access denied; not sending further spans\n")

    def test_403_clears_spans_that_were_still_queued(self):
        self.allow_stderr = True
        self.server.script = [(403, {"error": "Agent Observability is not enabled for this organization"}, {})]
        tracer = self.tracer(flush_interval=30, max_batch=500)
        # The sender cannot take a batch until this lock is released: six copies are definitely queued.
        with tracer._cond:
            for name in ("a", "b", "c"):
                with tracer.span(name):
                    pass
            tracer._max_batch = 2
        tracer.flush()
        self.assertEqual(len(self.server.requests), 1)
        self.assertEqual(len(self.server.spans()), 2)
        self.assertEqual(queued_copies(tracer), [])
        self.assertEqual(tracer._queue_bytes, 0)
        self.assertEqual(tracer._processed, tracer._enqueued)
        self.assertEqual(self.stderr.getvalue().count("not sending further spans"), 1)

    def test_backoff_queue_is_bounded_without_blocking_tracing(self):
        self.allow_stderr = True
        tracer = self.tracer(flush_interval=30, max_batch=500)
        with patch.object(cloudbyte_tracer, "_MAX_QUEUE_SPANS", 3), tracer._cond:
            for name in ("a", "b", "c", "d"):
                with tracer.span(name):
                    pass
            # Eviction takes the oldest ordinary copies first (here, start copies), so the end copies that close
            # the newest runs survive: b, c and d each keep a complete copy.
            self.assertEqual(len(queued_copies(tracer)), 3)
            self.assertEqual([item[1] for item in queued_copies(tracer)], ["b", "c", "d"])
            self.assertEqual(tracer._queue_bytes, sum(len(item[0]) for item in queued_copies(tracer)))
            self.assertEqual(tracer._processed, 5)
        tracer.flush()
        self.assertEqual(tracer._processed, tracer._enqueued)
        self.assertEqual(tracer._queue_bytes, 0)
        self.assertEqual(self.stderr.getvalue().count("queue full"), 1)

    def test_queue_bytes_are_bounded_even_before_the_count_limit(self):
        self.allow_stderr = True
        tracer = self.tracer(flush_interval=30, max_batch=500)
        with patch.object(cloudbyte_tracer, "_MAX_QUEUE_BYTES", 1500), tracer._cond:
            for index in range(10):
                with tracer.span("large-%d" % index, input="x" * 400):
                    pass
            self.assertLessEqual(tracer._queue_bytes, 1500)
            self.assertLess(len(queued_copies(tracer)), 20)
        tracer.flush()
        self.assertEqual(tracer._queue_bytes, 0)
        self.assertEqual(tracer._processed, tracer._enqueued)

    def test_a_span_larger_than_the_byte_cap_is_dropped_alone_without_evicting_the_queue(self):
        self.allow_stderr = True
        tracer = self.tracer(flush_interval=30, max_batch=500)
        with patch.object(cloudbyte_tracer, "_MAX_QUEUE_BYTES", 1500), tracer._cond:
            with tracer.span("small"):
                pass
            queued = [item[1] for item in queued_copies(tracer)]
            bytes_before = tracer._queue_bytes
            processed_before = tracer._processed
            self.assertEqual(queued, ["small", "small"])
            with tracer.span("huge", input="x" * 5000):
                pass  # its end copy, with the same input kept, is too big as well
            self.assertEqual([item[1] for item in queued_copies(tracer)], queued)  # the huge span's copies never entered
            self.assertEqual(tracer._queue_bytes, sum(len(item[0]) for item in queued_copies(tracer)))
            self.assertEqual(tracer._processed, processed_before)  # nothing was evicted
            self.assertEqual(tracer._queue_bytes, bytes_before)
        tracer.flush()
        self.assertEqual(tracer._processed, tracer._enqueued)
        self.assertEqual(self.stderr.getvalue().count("larger than the queue limit"), 1)

    def test_dead_server_never_raises(self):
        self.allow_stderr = True
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            closed_port = probe.getsockname()[1]
        tracer = self.tracer(base_url="http://127.0.0.1:%d" % closed_port, timeout=1.0)
        with tracer.span("t"):
            pass
        tracer.flush()
        tracer.shutdown()

        self.assertIn("cloudbyte_tracer:", self.stderr.getvalue())

    def test_batches_never_exceed_500_spans(self):
        tracer = self.tracer(flush_interval=30, max_batch=10000)
        for i in range(300):
            with tracer.span("s%d" % i):
                pass
        tracer.flush()

        sizes = [len(body["spans"]) for _, _, body in self.server.requests]
        self.assertEqual(sizes, [500, 100])

    def test_max_batch_sends_without_waiting_for_the_interval(self):
        tracer = self.tracer(flush_interval=30, max_batch=2)
        with tracer.span("t"):
            pass  # two copies: the batch is full
        self.assertTrue(wait_for(lambda: len(self.server.spans()) == 2))

    def test_interval_sends_a_partial_batch(self):
        tracer = self.tracer(flush_interval=0.05, max_batch=100)
        with tracer.span("t"):
            pass
        self.assertTrue(wait_for(lambda: len(self.server.spans()) == 2))

    def test_shutdown_flushes_and_stops_the_sender(self):
        tracer = self.tracer(flush_interval=30)
        with tracer.span("t"):
            pass
        tracer.shutdown()

        self.assertEqual(len(self.server.spans()), 2)
        self.assertEqual([t for t in threading.enumerate() if t.name == "cloudbyte-trace-sender"], [])

        # Still safe to use afterwards; nothing more is sent.
        with tracer.span("late"):
            pass
        tracer.flush()
        tracer.shutdown()
        self.assertEqual(len(self.server.spans()), 2)

    def test_spans_are_sent_at_process_exit(self):
        script = (
            "import sys\n"
            "from cloudbyte_tracer import Tracer\n"
            "tracer = Tracer('cbagt_TTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTT', sys.argv[1], flush_interval=30)\n"
            "with tracer.span('t', kind='agent'):\n"
            "    pass\n"  # no flush() or shutdown(): the atexit hook must send the queue
        )
        here = os.path.dirname(os.path.abspath(__file__))
        result = subprocess.run([sys.executable, "-c", script, self.server.url], cwd=here,
                                capture_output=True, text=True, timeout=30)

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(len(self.server.spans()), 2)


SERVER_BODY_LIMIT = 5 * 1024 * 1024  # the server answers 413 to a request body larger than 5 MB
SERVER_FIELD_LIMIT = 64 * 1024  # the server stores at most 64 KB of an input or output and flags the rest


class PriorityTests(TracerTestCase):
    """A root span's end copy closes its run, so it is sent ahead of everything else queued."""

    def test_root_end_is_sent_in_the_first_batch(self):
        tracer = self.tracer(flush_interval=30, max_batch=100)
        with tracer._cond:  # the sender cannot take a batch until this lock is released
            with tracer.span("job", kind="agent"):
                for index in range(250):
                    with tracer.span("step-%d" % index, kind="tool"):
                        pass
        tracer.flush()
        first = self.server.requests[0][2]["spans"]
        self.assertEqual(len(first), 100)
        self.assertEqual(first[0]["name"], "job")
        self.assertIsNotNone(first[0]["ended_at"])
        self.assertEqual(len(self.server.spans()), 252)  # 250 steps, plus the root's start and end copies

    def test_overflow_evicts_children_before_root_end_copies(self):
        self.allow_stderr = True
        tracer = self.tracer(flush_interval=30, max_batch=500)
        with patch.object(cloudbyte_tracer, "_MAX_QUEUE_SPANS", 5), tracer._cond:
            with tracer.span("job", kind="agent"):
                for index in range(6):
                    with tracer.span("step-%d" % index, kind="tool"):
                        pass
            # 8 copies for 5 places: the 3 oldest ordinary copies went (the start copy, step-0, step-1).
            self.assertEqual([item[1] for item in queued_copies(tracer)], ["job", "step-2", "step-3", "step-4", "step-5"])
            self.assertEqual(tracer._processed, 3)
        tracer.flush()
        self.assertIsNotNone(self.server.final("job")["ended_at"])

    def test_root_start_copy_keeps_fifo_order(self):
        tracer = self.tracer(flush_interval=30, max_batch=500)
        with tracer._cond:
            with tracer.span("job", kind="agent"):
                with tracer.span("child", kind="tool"):
                    pass
        tracer.flush()
        sent = [(span["name"], span["ended_at"] is not None) for span in self.server.spans()]
        # The end copy jumps the queue; the start copy keeps its place ahead of the child.
        self.assertEqual(sent, [("job", True), ("job", False), ("child", True)])


class OversizeTests(TracerTestCase):
    def test_huge_output_is_cut_and_the_whole_batch_still_arrives(self):
        tracer = self.tracer(flush_interval=30)
        with tracer.span("run", kind="agent", input={"ticket": 1}):
            with tracer.span("fetch_page", kind="tool", input={"url": "https://example.com"}) as tool:
                tool.set_output("y" * 6_000_000)
            with tracer.span("summarise", kind="llm", model="m"):
                pass
        tracer.flush()

        self.assertEqual(len(self.server.requests), 1)
        self.assertLess(self.server.sizes[0], SERVER_BODY_LIMIT)
        for name in ("run", "fetch_page", "summarise"):
            self.assertEqual(self.server.final(name)["status"], "ok")
        output = self.server.final("fetch_page")["output"]
        self.assertIsInstance(output, str)
        # The start of the serialised text, longer than the server keeps, so that the server flags it truncated.
        self.assertTrue(output.startswith('"yyyy'))
        self.assertGreater(len(output.encode("utf-8")), SERVER_FIELD_LIMIT)
        self.assertLessEqual(len(output.encode("utf-8")), 70000)

    def test_huge_input_is_cut_in_both_copies_without_splitting_a_character(self):
        tracer = self.tracer(flush_interval=30)
        with tracer.span("prompt", kind="llm", input="😀" * 100_000):  # 4 bytes each in UTF-8
            pass
        tracer.flush()

        for copy in (self.server.started("prompt"), self.server.final("prompt")):
            text = copy["input"]
            self.assertIsInstance(text, str)
            self.assertLessEqual(set(text), {'"', "😀"})  # whole characters only
            self.assertGreater(len(text.encode("utf-8")), SERVER_FIELD_LIMIT)
            self.assertLessEqual(len(text.encode("utf-8")), 70000)

    def test_a_value_that_fits_is_sent_unchanged(self):
        tracer = self.tracer()
        content = {"items": ["a" * 100] * 500}  # about 50 KB of JSON
        with tracer.span("fits", input=content) as s:
            s.set_output(content)
        tracer.flush()

        final = self.server.final("fits")
        self.assertEqual(final["input"], content)
        self.assertEqual(final["output"], content)

    def test_unprintable_oversize_content_is_still_cut(self):
        tracer = self.tracer(flush_interval=30)
        with tracer.span("odd", input=["z" * 1000] * 2000 + [float("nan")]):  # NaN: JSON cannot hold it
            pass
        tracer.flush()

        for copy in self.server.spans():
            self.assertIsInstance(copy["input"], str)
            self.assertLessEqual(len(copy["input"].encode("utf-8")), 70000)

    def test_batches_are_capped_by_encoded_size(self):
        tracer = self.tracer(flush_interval=30, max_batch=500)
        for i in range(40):
            with tracer.span("s%d" % i, input="a" * 60000):  # 80 copies of 60 KB: about 4.8 MB
                pass
        tracer.flush()

        self.assertGreater(len(self.server.requests), 1)
        self.assertTrue(all(size <= 4_000_000 for size in self.server.sizes), self.server.sizes)
        self.assertEqual(len(self.server.spans()), 80)
        self.assertEqual({span["name"] for span in self.server.spans()}, {"s%d" % i for i in range(40)})

    def test_a_span_bigger_than_the_cap_is_still_sent_on_its_own(self):
        tracer = self.tracer(flush_interval=30)
        with tracer.span("big") as s:
            s.set_status("error", error_message="e" * 4_500_000)  # one copy over the cap, under the server limit
        tracer.flush()

        self.assertEqual([len(body["spans"]) for _, _, body in self.server.requests], [2])
        self.assertEqual(len(self.server.final("big")["error"]["message"]), 4096)


class LimitTests(TracerTestCase):
    def test_name_and_model_are_clamped_to_200_characters(self):
        tracer = self.tracer()
        with tracer.span("n" * 250, kind="llm", model="m" * 300):
            pass
        with tracer.span("😀" * 250, model="😀" * 250):
            pass
        tracer.flush()

        by_name = {span["name"]: span for span in self.server.spans() if span["ended_at"] is not None}
        self.assertEqual(set(by_name), {"n" * 200, "😀" * 200})  # 200 characters, not 200 UTF-16 units
        self.assertEqual(by_name["n" * 200]["model"], "m" * 200)
        self.assertEqual(by_name["😀" * 200]["model"], "😀" * 200)
        for span in self.server.spans():  # the start copy agrees with the end copy
            self.assertIn(span["name"], by_name)

    def test_empty_name_becomes_span(self):
        tracer = self.tracer()
        with tracer.span(""):
            pass
        tracer.flush()

        self.assertEqual([span["name"] for span in self.server.spans()], ["span", "span"])


class ConstructorTests(unittest.TestCase):
    def test_default_base_url_is_cloudbyte(self):
        tracer = Tracer("cbagt_x")  # reads the default only: no span is created, so nothing is ever sent
        self.addCleanup(tracer.shutdown)
        self.assertEqual(tracer._base_url, "https://api.cloudbyte.ai")

    def test_constructor_fails_open_for_bad_args(self):
        for args in [("",), ("k", "ftp://x"), (None,)]:
            with contextlib.redirect_stderr(io.StringIO()):
                tracer = Tracer(*args)
            self.addCleanup(tracer.shutdown)
            self.assertTrue(tracer._disabled)


if __name__ == "__main__":
    unittest.main()

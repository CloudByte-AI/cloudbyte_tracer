import asyncio, contextlib, io, json, os, threading
from unittest.mock import patch
import pytest
from cloudbyte_tracer import Tracer, bind_context, init, trace, shutdown
from cloudbyte_tracer.limits import encode
from test_preview import FakeServer

KEY = "cbagt_" + "T" * 32
@pytest.fixture
def tracer():
    server = FakeServer()
    tracer = Tracer(KEY, server.url, flush_interval=30, retry_delays=(0,))
    yield tracer, server
    tracer.shutdown()
    server.stop()

def test_root_only_start(tracer):
    t,s=tracer
    with t.span("root"):
        with t.span("child"):
            pass
    t.flush()
    assert len(s.copies("root")) == 2
    assert len(s.copies("child")) == 1

def test_redaction_hook_and_failure(tracer):
    t,s=tracer
    def redact(value, field, span):
        if field == "output": raise RuntimeError("sensitive exception")
        return "redacted"
    t._redact=redact
    with t.span("root", input={"secret":"token"}, attributes={"label":"private"}) as span:
        span.set_output("secret")
    t.flush()
    final=s.final("root")
    assert final["input"] == "redacted" and "output" not in final
    assert final["attributes"]["label"] == "redacted"
    assert "secret" not in json.dumps(s.spans())

def test_attribute_budget_drops_largest(tracer):
    t,s=tracer
    with t.span("root", attributes={"small":"kept","large":"x"*40000}): pass
    t.flush()
    assert s.final("root")["attributes"] == {"small":"kept"}

def test_token_clamp_and_unicode_errors(tracer):
    t,s=tracer
    with t.span("root") as span:
        span.set_usage(input_tokens=9999999999999, output_tokens=-1)
        span.record_error(ValueError("😀"*5000))
    t.flush()
    span=s.final("root")
    assert span["usage"] == {"input_tokens":2147483647}
    assert len(span["error"]["message"]) == 4096

def test_401_permanently_stops(tracer):
    t,s=tracer
    s.status=401
    with t.span("root"): pass
    t.flush()
    with t.span("later"): pass
    t.flush()
    assert len(s.requests)==1 and t._disabled

def test_noop_and_environment():
    with patch.dict(os.environ,{"CLOUDBYTE_DISABLED":"true"}),contextlib.redirect_stderr(io.StringIO()) as err:
        t=Tracer(KEY)
        with t.span("ignored"): pass
        t.flush()
        assert t._disabled and not t._queue
        t.shutdown()
        assert err.getvalue().count("tracing disabled")==1

def test_thread_context_helper(tracer):
    t,s=tracer
    with t.span("root") as root:
        def work():
            with t.span("thread") as child: assert child.parent_span_id == root.span_id
        thread=threading.Thread(target=bind_context(work));thread.start();thread.join()
    t.flush()
    assert s.final("thread")["parent_span_id"]==root.span_id

@pytest.mark.asyncio
async def test_async_decorator_parallel_context(tracer):
    t,s=tracer
    @t.trace(kind="tool")
    async def worker():
        await asyncio.sleep(0)
        with t.span("child"): await asyncio.sleep(0)
        return 42
    with t.span("root") as root:
        assert await asyncio.gather(worker(),worker()) == [42,42]
    t.flush()
    workers=[x for x in s.spans() if x["name"]=="worker"]
    assert len(workers)==2 and all(x["parent_span_id"]==root.span_id for x in workers)
    assert len({x["parent_span_id"] for x in s.spans() if x["name"]=="child"})==2

def test_decorator_before_init_resolves_default(tracer):
    t,s=tracer
    @trace(kind="tool")
    def work(): return "done"
    import cloudbyte_tracer
    with patch.object(cloudbyte_tracer,"_default",t): assert work()=="done"
    t.flush()
    assert s.final("work")["kind"]=="tool"

def test_app_exception_identity(tracer):
    t,s=tracer
    error=ValueError("app failure")
    @t.trace()
    def work(): raise error
    with pytest.raises(ValueError) as got: work()
    assert got.value is error

def test_contract_cases():
    from pathlib import Path
    fixture=json.loads((Path(__file__).resolve().parents[2]/"contract"/"fixtures.json").read_text(encoding="utf8"))
    for case in fixture["cases"]:
        assert json.loads(encode(case["raw"])) == case["wire"]

def test_redirect_is_not_followed(tracer):
    t,s=tracer
    s.script=[(302,{}, {"Location":s.url+"/should-not-receive-key"})]
    with t.span("root"): pass
    t.flush()
    assert len(s.requests)==1

def test_input_snapshot_does_not_mutate(tracer):
    t,s=tracer
    value={"question":"before"}
    with t.span("root",input=value): value["question"]="after"
    t.flush()
    assert s.started("root")["input"]=={"question":"before"}
    assert s.final("root")["input"]=={"question":"after"}

def test_flush_waits_for_inflight_even_when_later_spans_are_evicted(tracer):
    from test_preview import wait_for
    import cloudbyte_tracer.transport as transport
    t,s=tracer
    gate=threading.Event();started=threading.Event();done=threading.Event()
    def send(batch):
        started.set()
        assert gate.wait(3)
    with patch.object(t,"_send",side_effect=send),patch.object(transport,"_MAX_QUEUE_SPANS",3):
        t._max_batch=2
        with t._cond:
            with t.span("inflight"): pass
        assert started.wait(2)
        def flush():
            t.flush(3)
            done.set()
        thread=threading.Thread(target=flush);thread.start()
        try:
            assert wait_for(lambda:t._flush_requested)
            t._max_batch=500
            with t._cond:
                for i in range(6):
                    with t.span("new"+str(i)): pass
            assert not done.wait(0.05), "evicting later spans must not settle an older in-flight batch"
        finally:
            gate.set();thread.join(3)
        assert done.is_set()
        t.flush()
        assert not t._pending

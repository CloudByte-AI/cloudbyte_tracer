import asyncio, json
import httpx, pytest
from cloudbyte_tracer import Tracer
from test_preview import FakeServer
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

KEY="cbagt_"+"T"*32
OPENAI={"id":"chatcmpl-test","object":"chat.completion","created":1,"model":"test-model","choices":[{"index":0,"message":{"role":"assistant","content":"hello"},"finish_reason":"stop"}],"usage":{"prompt_tokens":7,"completion_tokens":3,"total_tokens":10}}
ANTHROPIC={"id":"msg_test","type":"message","role":"assistant","model":"test-model","content":[{"type":"text","text":"hello"}],"stop_reason":"end_turn","stop_sequence":None,"usage":{"input_tokens":7,"output_tokens":3}}
@pytest.fixture
def setup():
    server=FakeServer()
    tracers=[]
    def create(**options):
        t=Tracer(KEY,server.url,flush_interval=30,**options);tracers.append(t);return t,server
    yield create
    for t in tracers: t.shutdown()
    server.stop()

def mocked_client(kind, async_=False, streaming=False):
    import openai, anthropic
    if kind == "anthropic":
        import httpx2 as httpx
    else:
        import httpx
    data=OPENAI if kind=="openai" else ANTHROPIC
    def respond(request):
        if not streaming: return httpx.Response(200,json=data)
        if kind=="openai":
            chunks=[{"id":"stream","object":"chat.completion.chunk","created":1,"model":"test-model","choices":[{"index":0,"delta":{"role":"assistant","content":"hello"},"finish_reason":None}]},{"id":"stream","object":"chat.completion.chunk","created":1,"model":"test-model","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":7,"completion_tokens":3,"total_tokens":10}}]
            text="".join("data: "+json.dumps(c)+"\n\n" for c in chunks)+"data: [DONE]\n\n"
        else:
            chunks=[("message_start",{"type":"message_start","message":{**ANTHROPIC,"content":[],"usage":{"input_tokens":7,"output_tokens":0}}}),("content_block_start",{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}),("content_block_delta",{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"hello"}}),("content_block_stop",{"type":"content_block_stop","index":0}),("message_delta",{"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":None},"usage":{"output_tokens":3}}),("message_stop",{"type":"message_stop"})]
            text="".join("event: "+e+"\ndata: "+json.dumps(c)+"\n\n" for e,c in chunks)
        return httpx.Response(200,text=text,headers={"content-type":"text/event-stream"})
    client=(httpx.AsyncClient if async_ else httpx.Client)(transport=httpx.MockTransport(respond))
    cls=(openai.AsyncOpenAI if async_ else openai.OpenAI) if kind=="openai" else (anthropic.AsyncAnthropic if async_ else anthropic.Anthropic)
    return cls(api_key="mock-provider-key",http_client=client,max_retries=0)

@pytest.mark.parametrize("kind",["openai","anthropic"])
@pytest.mark.parametrize("streaming",[False,True])
def test_sync_provider_and_stream(setup,kind,streaming):
    t,s=setup(auto_capture=[kind])
    client=mocked_client(kind,streaming=streaming)
    try:
        with t.span("manual",kind="agent") as root:
            if kind=="openai": result=client.chat.completions.create(model="test-model",messages=[{"role":"user","content":"hi"}],stream=streaming)
            else: result=client.messages.create(model="test-model",max_tokens=10,messages=[{"role":"user","content":"hi"}],stream=streaming)
            if streaming: assert len(list(result))>0
        t.flush()
        llms=[x for x in s.spans() if x["kind"]=="llm" and x.get("ended_at")]
        assert len(llms)==1, s.spans()
        span=llms[0]
        assert span["parent_span_id"]==root.span_id and span["trace_id"]==root.trace_id
        assert span["model"]=="test-model"
        assert span["usage"]["input_tokens"]==7 and span["usage"]["output_tokens"]==3
        assert "hello" in json.dumps(span.get("output"))
    finally: client.close()

@pytest.mark.asyncio
@pytest.mark.parametrize("kind",["openai","anthropic"])
@pytest.mark.parametrize("streaming",[False,True])
async def test_async_provider_and_stream(setup,kind,streaming):
    t,s=setup(auto_capture=[kind]);client=mocked_client(kind,async_=True,streaming=streaming)
    try:
        with t.span("manual",kind="agent") as root:
            if kind=="openai": result=await client.chat.completions.create(model="test-model",messages=[{"role":"user","content":"hi"}],stream=streaming)
            else: result=await client.messages.create(model="test-model",max_tokens=10,messages=[{"role":"user","content":"hi"}],stream=streaming)
            if streaming:
                chunks=[chunk async for chunk in result];assert chunks
        t.flush()
        spans=[x for x in s.spans() if x["kind"]=="llm" and x.get("ended_at")]
        assert len(spans)==1 and spans[0]["parent_span_id"]==root.span_id
    finally: await client.close()

def test_manual_langgraph_openai_prototype(setup):
    from langgraph.graph import StateGraph, START, END
    from typing import TypedDict
    t,s=setup(auto_capture=["langchain","openai"])
    client=mocked_client("openai")
    class State(TypedDict):
        result: str
    def respond(state):
        answer=client.chat.completions.create(model="test-model",messages=[{"role":"user","content":"hi"}])
        return {"result":answer.choices[0].message.content}
    graph=StateGraph(State);graph.add_node("respond",respond);graph.add_edge(START,"respond");graph.add_edge("respond",END);app=graph.compile()
    try:
        with t.span("manual",kind="agent") as root: assert app.invoke({"result":""})["result"]=="hello"
        t.flush()
        ended=[x for x in s.spans() if x.get("ended_at")]
        assert len(ended)>=4, ended
        assert {x["trace_id"] for x in ended}=={root.trace_id}
        llm=next(x for x in ended if x["kind"]=="llm")
        by_id={x["span_id"]:x for x in ended}
        assert by_id[llm["parent_span_id"]]["name"]=="respond"
        assert next(x for x in ended if x["name"]=="LangGraph")["parent_span_id"]==root.span_id
        assert next(x for x in ended if x["name"]=="LangGraph")["kind"]=="custom"
    finally: client.close()

def test_private_provider_does_not_replace_global(setup):
    before=trace.get_tracer_provider()
    t,s=setup(auto_capture=[] ,tracer_provider=TracerProvider(shutdown_on_exit=False))
    assert trace.get_tracer_provider() is before
    with t.span("root",kind="agent"):
        with t.span("manual-tool",kind="tool"): pass
    t.flush()
    assert len(s.spans())==3
    t.shutdown()
    external=t._bridge

def test_content_disabled_in_instrumentation_and_wire(setup):
    t,s=setup(auto_capture=["openai"],capture_content=False)
    client=mocked_client("openai")
    with t.span("root",input="SECRET"):
        client.chat.completions.create(model="test-model",messages=[{"role":"user","content":"SECRET"}])
    t.flush();client.close()
    assert all("input" not in x and "output" not in x for x in s.spans())
    assert "SECRET" not in json.dumps(s.spans())

def test_root_chain_mapping(setup):
    t,s=setup(auto_capture=["langchain"])
    with t._bridge.tracer.start_as_current_span("root-chain",attributes={"openinference.span.kind":"CHAIN"}):
        with t.span("nested",kind="tool"): pass
    t.flush()
    assert s.final("root-chain")["kind"]=="agent"
    assert s.final("nested")["parent_span_id"]==s.final("root-chain")["span_id"]

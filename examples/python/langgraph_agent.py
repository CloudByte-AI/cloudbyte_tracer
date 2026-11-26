"""Manual root -> real LangGraph -> OpenAI client using an offline HTTP response."""
import json
import httpx
from cloudbyte_tracer import init
response={"id":"chatcmpl-test","object":"chat.completion","created":1,"model":"test-model","choices":[{"index":0,"message":{"role":"assistant","content":"hello"},"finish_reason":"stop"}],"usage":{"prompt_tokens":7,"completion_tokens":3,"total_tokens":10}}
from openai import OpenAI
from langgraph.graph import StateGraph, START, END
from typing import TypedDict
tracer=init(auto_capture=["langchain","openai"])
client=OpenAI(api_key="mock-provider-key",http_client=httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(200,json=response))))
class State(TypedDict):
    result:str

def respond(state):
    return {"result":client.chat.completions.create(model="test-model",messages=[{"role":"user","content":"hi"}]).choices[0].message.content}
graph=StateGraph(State);graph.add_node("respond",respond);graph.add_edge(START,"respond");graph.add_edge("respond",END);app=graph.compile()
try:
    with tracer.span("request",kind="agent"):
        print(app.invoke({"result":""})["result"])
finally:
    client.close()
    tracer.shutdown()

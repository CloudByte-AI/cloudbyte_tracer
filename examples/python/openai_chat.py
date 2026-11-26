"""Offline provider response; tracing still uses your configured CloudByte endpoint."""
import json
import httpx
from cloudbyte_tracer import init
response={"id":"chatcmpl-test","object":"chat.completion","created":1,"model":"test-model","choices":[{"index":0,"message":{"role":"assistant","content":"hello"},"finish_reason":"stop"}],"usage":{"prompt_tokens":7,"completion_tokens":3,"total_tokens":10}}
from openai import OpenAI
tracer=init(auto_capture=["openai"])
client=OpenAI(api_key="mock-provider-key",http_client=httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(200,json=response))))
try:
    with tracer.span("chat",kind="agent"):
        print(client.chat.completions.create(model="test-model",messages=[{"role":"user","content":"hi"}]).choices[0].message.content)
finally:
    client.close()
    tracer.shutdown()

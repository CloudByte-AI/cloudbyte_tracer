"""Offline provider response; no Anthropic API key required."""
from anthropic import Anthropic
try:
    import httpx2 as httpx
except ImportError:
    import httpx
from cloudbyte_tracer import init
response={"id":"msg_test","type":"message","role":"assistant","model":"test-model","content":[{"type":"text","text":"hello"}],"stop_reason":"end_turn","stop_sequence":None,"usage":{"input_tokens":7,"output_tokens":3}}
tracer=init(auto_capture=["anthropic"])
client=Anthropic(api_key="mock-provider-key",http_client=httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(200,json=response))))
try:
    with tracer.span("chat",kind="agent"):
        print(client.messages.create(model="test-model",max_tokens=10,messages=[{"role":"user","content":"hi"}]).content[0].text)
finally:
    client.close()
    tracer.shutdown()

# cloudbyte-tracer

Python and Node.js SDKs for CloudByte Agent Observability. One trace is one run. Manual steps and optional OpenInference integrations record the step tree, models, token usage, content and errors.

Requires Python 3.10+ or Node.js 20+. Core has zero runtime dependencies. Auto-capture supports OpenAI, Anthropic and LangChain/LangGraph. Content capture is enabled by default.

## Install

~~~sh
pip install cloudbyte-tracer
npm install cloudbyte-tracer
~~~

To record OpenAI, Anthropic or LangChain/LangGraph calls automatically, install the matching Python extra, for example `pip install "cloudbyte-tracer[openai]"` (also `[anthropic]`, `[langchain]`, `[all]`). Node needs a few OpenTelemetry peer packages; see [Auto-capture](https://github.com/CloudByte-AI/cloudbyte_tracer/blob/main/docs/auto-capture.md).

Create an agent in CloudByte Agent Observability and set its key as CLOUDBYTE_AGENT_KEY in your application's environment. Do not paste keys into source files. Set CLOUDBYTE_BASE_URL only if your CloudByte server is not https://api.cloudbyte.ai, the default.

~~~python
from cloudbyte_tracer import init
tracer = init()
with tracer.span("request", kind="agent", input={"question": "hello"}) as run:
    with tracer.span("lookup", kind="tool") as step:
        step.set_output({"answer": "hello"})
    run.set_output("hello")
tracer.shutdown()
~~~

~~~js
import { init } from "cloudbyte-tracer";
const tracer = init();
await tracer.withSpan("request", { kind: "agent" }, async run => {
  await tracer.withSpan("lookup", { kind: "tool" }, step => step.setOutput("hello"));
  run.setOutput("hello");
});
await tracer.shutdown();
~~~

## Documentation

- [Getting started](https://github.com/CloudByte-AI/cloudbyte_tracer/blob/main/docs/getting-started.md)
- [Manual tracing](https://github.com/CloudByte-AI/cloudbyte_tracer/blob/main/docs/manual-tracing.md)
- [Auto-capture](https://github.com/CloudByte-AI/cloudbyte_tracer/blob/main/docs/auto-capture.md)
- [Configuration and limits](https://github.com/CloudByte-AI/cloudbyte_tracer/blob/main/docs/configuration.md)
- [Privacy and redaction](https://github.com/CloudByte-AI/cloudbyte_tracer/blob/main/docs/privacy-and-redaction.md)
- [Troubleshooting](https://github.com/CloudByte-AI/cloudbyte_tracer/blob/main/docs/troubleshooting.md)
- [Development, tests and releases](https://github.com/CloudByte-AI/cloudbyte_tracer/blob/main/CONTRIBUTING.md)

All provider examples use mocked responses by default and require no provider keys. Set CLOUDBYTE_DISABLED=true for completely offline execution. With a CloudByte key configured, they send traces to your selected endpoint.

## Delivery guarantees

Sending is best effort, with a bounded in-memory queue and retry budget. Shutdown flushes with a deadline; delivery is not durable. Root spans send start and end copies, children send only end copies. A root's end copy is sent first, so a run is marked finished even if a deadline drops some of its steps. Batch jobs that create many steps should call `flush(timeout=60)` (Python) or `await flush(60000)` (Node) before exiting. A 401 or 403 stops that tracer permanently; restart after correcting access.

Apache-2.0.

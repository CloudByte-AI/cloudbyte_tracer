# cloudbyte-tracer

Python and Node.js SDKs for CloudByte Agent Observability, version 0.1.0. One trace is one run. Manual steps and optional OpenInference integrations record the step tree, models, token usage, content and errors.

Requires Python 3.10+ or Node.js 20+. Core has zero runtime dependencies. Auto-capture supports OpenAI, Anthropic and LangChain/LangGraph. Content capture is enabled by default.

## Install a local build

Distribution is undecided. These packages work from local files or an internal artifact server; no registry is required.

~~~sh
pip install ./cloudbyte_tracer-0.1.0-py3-none-any.whl
npm install ./cloudbyte-tracer-0.1.0.tgz
~~~

Set CLOUDBYTE_AGENT_KEY and CLOUDBYTE_BASE_URL in your application's environment. Do not paste keys into source files. The URL defaults to https://api.cloudbyte.ai.

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

- [Getting started](../docs/getting-started.md)
- [Manual tracing](../docs/manual-tracing.md)
- [Auto-capture](../docs/auto-capture.md)
- [Configuration and limits](../docs/configuration.md)
- [Privacy and redaction](../docs/privacy-and-redaction.md)
- [Troubleshooting](../docs/troubleshooting.md)
- [Development, tests and releases](CONTRIBUTING.md)

All provider examples use mocked responses by default and require no provider keys. Set CLOUDBYTE_DISABLED=true for completely offline execution. With a CloudByte key configured, they send traces to your selected endpoint.

## Delivery guarantees

Sending is best effort, with a bounded in-memory queue and retry budget. Shutdown flushes with a deadline; delivery is not durable. Root spans send start and end copies, children send only end copies. A 401 or 403 stops that tracer permanently; restart after correcting access.

Apache-2.0. No SDK packages have been published by this work.

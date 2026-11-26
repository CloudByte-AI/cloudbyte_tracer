# Getting started

Install a wheel or tarball from your team's artifact store: pip install ./cloudbyte_tracer-0.1.0-py3-none-any.whl or npm install ./cloudbyte-tracer-0.1.0.tgz. No registry is required; public/private distribution is undecided.

Create an agent in CloudByte Agent Observability. Store its key in CLOUDBYTE_AGENT_KEY through your secret manager, and set CLOUDBYTE_BASE_URL to the server origin. For local development that can be http://localhost:4242. Do not append /v1/agent-spans. init() reads these variables.

Use the README's manual example. One root creates one run; nested spans are steps. Call shutdown before a short process exits. Node serverless handlers must await flush before returning. See auto-capture.md for optional dependencies.

examples/python and examples/js provide manual, OpenAI, Anthropic and LangGraph demos. Provider HTTP responses are mocked, so no provider key is needed. Set CLOUDBYTE_DISABLED=true for completely offline execution. With a CloudByte key, examples send traces to your configured endpoint; use your own authorised server.

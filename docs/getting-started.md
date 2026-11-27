# Getting started

Install from PyPI or npm: `pip install cloudbyte-tracer` or `npm install cloudbyte-tracer`. Python 3.10+ or Node.js 20+.

Create an agent in CloudByte Agent Observability. Store its key in CLOUDBYTE_AGENT_KEY through your secret manager. The SDKs send to https://api.cloudbyte.ai by default; set CLOUDBYTE_BASE_URL only for another server origin, for example http://localhost:4242 in local development. Do not append /v1/agent-spans. init() reads these variables. The agent's page in CloudByte shows the same steps with copyable commands.

Use the README's manual example. One root creates one run; nested spans are steps. Call shutdown before a short process exits. Node serverless handlers must await flush before returning. See auto-capture.md for optional dependencies.

examples/python and examples/js provide manual, OpenAI, Anthropic and LangGraph demos. Provider HTTP responses are mocked, so no provider key is needed. Set CLOUDBYTE_DISABLED=true for completely offline execution. With a CloudByte key, examples send traces to your configured endpoint; use your own authorised server.

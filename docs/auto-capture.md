# Auto-capture

Optional capture uses OpenInference's maintained instrumentations and the core's existing queue. There are no handwritten provider adapters or OTLP endpoint.

Python: install a local wheel extra, for example pip install './cloudbyte_tracer-0.1.0-py3-none-any.whl[openai]'. Extras: openai, anthropic, langchain, all. Install the provider/framework client your app uses. Then init(auto_capture=["openai", "anthropic", "langchain"]). true enables installed integrations. Named missing integrations warn. Sync/async completions and consumed streams are tested; consume/close streams so spans finish.

Node: install @opentelemetry/api, @opentelemetry/sdk-trace-base, @opentelemetry/context-async-hooks and the selected @arizeai/openinference-instrumentation-openai, -anthropic or -langchain optional peers alongside your tarball and provider clients.

ESM may load different instances from require. Explicitly pass the modules your application uses:

~~~js
import {init} from "cloudbyte-tracer";
import OpenAI from "openai";
import * as callbacks from "@langchain/core/callbacks/manager";
const tracer = init({autoCapture: ["openai", "langchain"],
  modules: {openai: OpenAI, langchain: callbacks}});
~~~

For Anthropic pass modules: {anthropic: Anthropic}. CommonJS clients are resolved automatically. Initialise before application traffic. Browser JS is out of scope.

## Provider coexistence and graph context

Default providers are private and never replace an app's global provider. Node reuses an existing global context manager, installing AsyncLocalStorageContextManager only when absent. It leaves that manager installed at shutdown because other OTel users may rely on it.

Python supports tracer_provider=existing_provider and disables only its processor at shutdown. Node supports mutable providers with addSpanProcessor. JS OTel v2 requires processors at construction, so use a synchronous factory:

~~~js
import {BasicTracerProvider} from "@opentelemetry/sdk-trace-base";
let applicationProvider;
const tracer = init({autoCapture: ["openai"], modules: {openai: OpenAI},
  tracerProvider: processor => {
    applicationProvider = new BasicTracerProvider({spanProcessors: [processor]});
    return applicationProvider;
  }});
~~~

The app owns registration/shutdown of that provider. A previously constructed v2 provider cannot accept another processor. Shared providers export all spans they process, so choose deliberately.

OpenInference LangChain callbacks create framework spans without activating them. The context bridge finds the current framework run and uses it only when existing OTel context is its ancestor; deeper manual contexts are preserved. Actual OTel IDs/parents remain intact. The exporter does not invent links. The manual -> real LangGraph -> direct OpenAI prototype is tested in both languages.

Python uses OpenInference's public get_current_span. JS uses LangChain's singleton/getParentRunId and OpenInference's run-to-span map, plus its manual unpatch hook for shutdown. The last two are upstream implementation details: dependency ranges are bounded, development is locked, and upgrades require integration tests. Existing instrumentations are skipped with a warning. Use one patching owner per provider module and one auto-capture tracer per process.

Mapping: LLM -> llm; TOOL -> tool; RETRIEVER/RERANKER -> retrieval; AGENT -> agent. Root CHAIN -> agent, nested CHAIN -> custom; other kinds -> custom. Model/tokens, timestamps, errors and IDs map directly. Flattened message attributes become role/content arrays; otherwise JSON input.value/output.value is parsed. Content namespaces are removed from generic attributes.

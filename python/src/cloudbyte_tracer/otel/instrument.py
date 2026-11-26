"""Private provider, shared OTel context; never replaces the app's provider."""
import importlib, json
from importlib.metadata import version
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.trace import Status, StatusCode
from openinference.instrumentation import TraceConfig
from .processor import CloudByteSpanProcessor
from .context import ContextProvider
from ..transport import _log

INTEGRATIONS = {"openai": ("openinference.instrumentation.openai", "OpenAIInstrumentor"),
                "anthropic": ("openinference.instrumentation.anthropic", "AnthropicInstrumentor"),
                "langchain": ("openinference.instrumentation.langchain", "LangChainInstrumentor")}
_active_bridge = None

class Bridge:
    def __init__(self, owner, integrations, provider=None):
        global _active_bridge
        if _active_bridge is not None:
            raise RuntimeError("one auto-capture tracer per process")
        self.owner = owner
        self.owns_provider = provider is None
        self.provider = provider if provider is not None else TracerProvider(resource=Resource.create({"service.name": owner._service_name or "cloudbyte-agent"}), shutdown_on_exit=False)
        self.processor = CloudByteSpanProcessor(owner)
        self.provider.add_span_processor(self.processor)
        self.context_provider = ContextProvider(self.provider, self.processor.parents)
        self.tracer = self.context_provider.get_tracer("cloudbyte_tracer", version("cloudbyte-tracer"))
        self.instrumentors = []
        config = TraceConfig(hide_inputs=not owner._capture_content, hide_outputs=not owner._capture_content, hide_llm_invocation_parameters=not owner._capture_content, hide_llm_tools=not owner._capture_content, hide_retrieval_documents=not owner._capture_content)
        names = INTEGRATIONS if integrations is True else (integrations or [])
        for name in names:
            try:
                mod, cls = INTEGRATIONS[name]
                inst = getattr(importlib.import_module(mod), cls)()
                if inst.is_instrumented_by_opentelemetry:
                    _log("integration already instrumented; skipping " + name)
                    continue
                inst.instrument(tracer_provider=self.context_provider, config=config)
                if inst.is_instrumented_by_opentelemetry:
                    self.instrumentors.append(inst)
            except Exception:
                if integrations is not True:
                    _log("integration unavailable: " + str(name))
        _active_bridge = self

    def start(self, manual):
        attrs = dict(manual._attributes)
        attrs["openinference.span.kind"] = {"agent": "AGENT", "llm": "LLM", "tool": "TOOL", "retrieval": "RETRIEVER"}.get(manual._kind, "UNKNOWN")
        if manual._input is not None and self.owner._capture_content:
            attrs["input.value"] = json.dumps(manual._input, default=str)
        if manual._model is not None:
            attrs["llm.model_name"] = manual._model
        context = None
        if manual._explicit_parent is not None and getattr(manual._explicit_parent, "_otel_span", None) is not None:
            context = trace.set_span_in_context(manual._explicit_parent._otel_span)
        span = self.tracer.start_span(manual._name, context=context, attributes=attrs)
        cm = trace.use_span(span, end_on_exit=False, record_exception=False, set_status_on_exception=False)
        cm.__enter__()
        return span, cm

    def end(self, manual, exc):
        span = manual._otel_span
        try:
            for k, v in manual._attributes.items():
                span.set_attribute(k, v)
            if manual._output is not None and self.owner._capture_content:
                span.set_attribute("output.value", json.dumps(manual._output, default=str))
            if manual._model is not None:
                span.set_attribute("llm.model_name", manual._model)
            usage = {"input_tokens": "prompt", "output_tokens": "completion", "cache_read_tokens": "prompt_details.cache_read", "cache_write_tokens": "prompt_details.cache_write"}
            for k, v in manual._usage.items():
                span.set_attribute("llm.token_count." + usage[k], v)
            if manual._error:
                span.add_event("exception", {"exception.type": manual._error["type"], "exception.message": manual._error["message"]})
            code = StatusCode.ERROR if manual._status == "error" else StatusCode.UNSET if manual._status == "unset" else StatusCode.OK
            span.set_status(Status(code))
        finally:
            manual._otel_context.__exit__(None, None, None)
            span.end()

    def shutdown(self):
        global _active_bridge
        self.processor.shutdown()
        for inst in self.instrumentors:
            try:
                inst.uninstrument()
            except Exception:
                pass
        if self.owns_provider:
            self.provider.shutdown()
        if _active_bridge is self:
            _active_bridge = None

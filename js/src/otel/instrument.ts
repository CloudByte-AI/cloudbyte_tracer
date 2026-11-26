import { createRequire } from 'node:module';
import type { Tracer, Span } from '../core.js';
import { CloudByteSpanProcessor } from './processor.js';
import { log } from '../transport.js';
import { contextProvider } from './context.js';
import { version } from '../version.js';
const requireOptional = createRequire(
  typeof __filename === 'string' ? __filename : import.meta.url,
);
const integrations: Record<string, [string, string, string]> = {
  openai: ['@arizeai/openinference-instrumentation-openai', 'OpenAIInstrumentation', 'openai'],
  anthropic: [
    '@arizeai/openinference-instrumentation-anthropic',
    'AnthropicInstrumentation',
    '@anthropic-ai/sdk',
  ],
  langchain: [
    '@arizeai/openinference-instrumentation-langchain',
    'LangChainInstrumentation',
    '@langchain/core/callbacks/manager',
  ],
};
let active: Bridge | undefined;
export class Bridge {
  readonly provider: any;
  readonly tracer: any;
  private api: any;
  private processor: CloudByteSpanProcessor;
  private instruments: any[] = [];
  private ownsProvider: boolean;
  constructor(private owner: Tracer) {
    if (active) throw Error('Only one auto-capture tracer per process');
    this.api = requireOptional('@opentelemetry/api');
    const { AsyncLocalStorageContextManager } = requireOptional(
      '@opentelemetry/context-async-hooks',
    );
    const manager = new AsyncLocalStorageContextManager().enable();
    if (!this.api.context.setGlobalContextManager(manager)) manager.disable();
    this.processor = new CloudByteSpanProcessor(owner);
    this.ownsProvider = !owner.options.tracerProvider;
    if (this.ownsProvider) {
      const { BasicTracerProvider } = requireOptional('@opentelemetry/sdk-trace-base');
      this.provider = new BasicTracerProvider({ spanProcessors: [this.processor] });
    } else if (typeof owner.options.tracerProvider === 'function')
      this.provider = owner.options.tracerProvider(this.processor);
    else {
      this.provider = owner.options.tracerProvider;
      if (typeof this.provider.addSpanProcessor !== 'function')
        throw Error('Use a provider factory for OpenTelemetry v2');
      this.provider.addSpanProcessor(this.processor);
    }
    const providerForContext = contextProvider(
      this.provider,
      this.api,
      this.processor.parents,
      requireOptional,
    );
    this.tracer = providerForContext.getTracer('cloudbyte_tracer', version);
    const names =
      owner.options.autoCapture === true
        ? Object.keys(integrations)
        : owner.options.autoCapture || [];
    for (const name of names) {
      try {
        const [pkg, cls, moduleName] = integrations[name];
        const plugin = requireOptional(pkg);
        if (plugin.isPatched?.()) {
          log('integration already instrumented; skipping ' + name);
          continue;
        }
        const inst = new plugin[cls]({
          instrumentationConfig: { enabled: false },
          tracerProvider: providerForContext,
          traceConfig: {
            hideInputs: !owner.options.captureContent,
            hideOutputs: !owner.options.captureContent,
            hideLLMInvocationParameters: !owner.options.captureContent,
            hideLLMTools: !owner.options.captureContent,
          },
        });
        const module =
          (owner.options.modules as Record<string, unknown> | undefined)?.[name] ??
          requireOptional(moduleName);
        inst.manuallyInstrument(module);
        this.instruments.push({ inst, module, name });
      } catch {
        if (owner.options.autoCapture !== true) log('integration unavailable: ' + name);
      }
    }
    // eslint-disable-next-line @typescript-eslint/no-this-alias -- records the one process-wide owner
    active = this;
  }
  run<T>(manual: Span, fn: () => T): T {
    let nextContext: any;
    try {
      const attrs: any = {
        ...manual.data.attributes,
        'openinference.span.kind':
          (
            { agent: 'AGENT', llm: 'LLM', tool: 'TOOL', retrieval: 'RETRIEVER' } as Record<
              string,
              string
            >
          )[manual.data.kind] ?? 'UNKNOWN',
      };
      if (manual.data.input !== undefined && this.owner.options.captureContent) {
        try {
          attrs['input.value'] = JSON.stringify(manual.data.input);
        } catch {}
      }
      if (manual.data.model !== undefined) attrs['llm.model_name'] = manual.data.model;
      let ctx = this.api.context.active();
      if (manual.options.parent?.otelSpan)
        ctx = this.api.trace.setSpan(ctx, manual.options.parent.otelSpan);
      manual.otelSpan = this.tracer.startSpan(manual.data.name, { attributes: attrs }, ctx);
      const sc = manual.otelSpan.spanContext(),
        parent = this.api.trace.getSpanContext(ctx);
      manual.data.trace_id = sc.traceId;
      manual.data.span_id = sc.spanId;
      manual.data.parent_span_id =
        manual.otelSpan.parentSpanContext?.spanId ?? parent?.spanId ?? null;
      nextContext = this.api.trace.setSpan(ctx, manual.otelSpan);
    } catch {
      manual.otelSpan = undefined;
      if (!manual.parentSpanId) this.owner.enqueue({ ...manual.data });
      return fn();
    }
    return this.api.context.with(nextContext, fn);
  }
  end(manual: Span) {
    const span = manual.otelSpan;
    try {
      span.setAttributes(manual.data.attributes ?? {});
      if (manual.data.output !== undefined && this.owner.options.captureContent) {
        try {
          span.setAttribute('output.value', JSON.stringify(manual.data.output));
        } catch {}
      }
      if (manual.data.model !== undefined) span.setAttribute('llm.model_name', manual.data.model);
      const keys: Record<string, string> = {
        input_tokens: 'prompt',
        output_tokens: 'completion',
        cache_read_tokens: 'prompt_details.cache_read',
        cache_write_tokens: 'prompt_details.cache_write',
      };
      for (const [k, v] of Object.entries(manual.data.usage ?? {}))
        if (keys[k]) span.setAttribute('llm.token_count.' + keys[k], v);
      if (manual.data.error)
        span.addEvent('exception', {
          'exception.type': manual.data.error.type,
          'exception.message': manual.data.error.message,
        });
      span.setStatus({
        code: manual.data.status === 'error' ? 2 : manual.data.status === 'unset' ? 0 : 1,
      });
    } finally {
      span.end();
    }
  }
  async shutdown() {
    void this.processor.shutdown();
    for (const { inst, module, name } of this.instruments) {
      try {
        inst.disable();
        if (name === 'langchain') inst.unpatch(module);
      } catch {}
    }
    if (active === this) active = undefined;
    if (this.ownsProvider) await this.provider.shutdown();
  }
}

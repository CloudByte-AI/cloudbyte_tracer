import { randomBytes } from 'node:crypto';
import { activeSpan } from './context.js';
import { Transport, log } from './transport.js';
import { clampText, text } from './limits.js';
import { Bridge } from './otel/instrument.js';
import type { Status, TracerOptions, SpanOptions, WireSpan } from './types.js';
export class Span {
  data: WireSpan;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any -- the optional OTel span; the core must not import OTel types
  otelSpan?: any;
  constructor(
    readonly tracer: Tracer,
    name: string,
    readonly options: SpanOptions = {},
  ) {
    const parent = options.parent ?? activeSpan.getStore();
    this.data = {
      trace_id: parent?.tracer === tracer ? parent.traceId : randomBytes(16).toString('hex'),
      span_id: randomBytes(8).toString('hex'),
      parent_span_id: parent?.tracer === tracer ? parent.spanId : null,
      name: clampText(name) || 'span',
      kind: ['agent', 'llm', 'tool', 'retrieval', 'custom'].includes(options.kind ?? '')
        ? options.kind!
        : 'custom',
      started_at: new Date().toISOString(),
      ended_at: null,
      attributes: { ...options.attributes },
    };
    if (tracer.options.captureContent) this.data.input = options.input;
    if (options.model !== undefined) this.data.model = clampText(options.model);
  }
  get traceId() {
    return this.data.trace_id;
  }
  get spanId() {
    return this.data.span_id;
  }
  get parentSpanId() {
    return this.data.parent_span_id;
  }
  setOutput(value: unknown) {
    if (this.tracer.options.captureContent) this.data.output = value;
  }
  setModel(model: string) {
    this.data.model = clampText(model);
  }
  setUsage(usage: {
    input_tokens?: number;
    output_tokens?: number;
    cache_read_tokens?: number;
    cache_write_tokens?: number;
  }) {
    this.data.usage = { ...this.data.usage, ...usage } as Record<string, number>;
  }
  setAttribute(key: string, value: unknown) {
    try {
      (this.data.attributes ??= {})[text(key)] = typeof value === 'object' ? text(value) : value;
    } catch {}
  }
  setStatus(status: Status) {
    if (['ok', 'error', 'unset'].includes(status)) this.data.status = status;
  }
  recordError(error: unknown) {
    this.data.status = 'error';
    this.data.error = {
      type: error instanceof Error ? error.name : 'Error',
      message: error instanceof Error ? error.message : text(error),
    };
  }
}
export class Tracer {
  readonly options: TracerOptions;
  readonly transport: Transport;
  readonly bridge?: Bridge;
  private stopped = false;
  private beforeExit = () => {
    if (!this.stopped) void this.flush();
  };
  constructor(options: TracerOptions = {}) {
    const truth = (s?: string) => ['true', '1', 'yes', 'on'].includes(s?.toLowerCase() ?? '');
    this.options = {
      ...options,
      apiKey: options.apiKey ?? process.env.CLOUDBYTE_AGENT_KEY,
      baseUrl: (
        options.baseUrl ??
        process.env.CLOUDBYTE_BASE_URL ??
        'https://api.cloudbyte.ai'
      ).replace(/\/+$/, ''),
      captureContent:
        options.captureContent ??
        !['false', '0', 'no', 'off'].includes(
          process.env.CLOUDBYTE_CAPTURE_CONTENT?.toLowerCase() ?? '',
        ),
    };
    let invalid: boolean;
    try {
      const u = new URL(this.options.baseUrl!);
      invalid =
        !['http:', 'https:'].includes(u.protocol) ||
        !!u.username ||
        !!u.password ||
        !!u.search ||
        !!u.hash;
    } catch {
      invalid = true;
    }
    this.transport = new Transport(this.options);
    if (
      !this.options.apiKey?.trim() ||
      options.disabled ||
      truth(process.env.CLOUDBYTE_DISABLED) ||
      invalid
    ) {
      this.transport.disabled = true;
      log('tracing disabled (missing key or disabled configuration)');
    } else if (options.autoCapture || options.tracerProvider) {
      try {
        this.bridge = new Bridge(this);
      } catch {
        log('auto-capture unavailable; check optional dependencies');
      }
    }
    process.on('beforeExit', this.beforeExit);
  }
  withSpan<T>(name: string, options: SpanOptions, fn: (span: Span) => T): T {
    const span = new Span(this, name, options);
    const invoke = () => {
      const finish = (failed = false, error?: unknown) => {
        try {
          if (failed) span.recordError(error);
          span.data.ended_at = new Date().toISOString();
          span.data.status ??= 'ok';
          if (this.bridge && span.otelSpan) this.bridge.end(span);
          else this.enqueue(span.data);
        } catch {}
      };
      try {
        const result = fn(span);
        if (result && typeof (result as { then?: unknown }).then === 'function')
          return Promise.resolve(result).then(
            (v) => {
              finish();
              return v;
            },
            (e) => {
              finish(true, e);
              throw e;
            },
          ) as T;
        finish();
        return result;
      } catch (error) {
        finish(true, error);
        throw error;
      }
    };
    return activeSpan.run(span, () => {
      if (this.bridge) return this.bridge.run(span, invoke);
      if (!span.parentSpanId) this.enqueue({ ...span.data });
      return invoke();
    });
  }
  // eslint-disable-next-line @typescript-eslint/no-explicit-any -- standard idiom for wrapping any function signature
  trace<F extends (...args: any[]) => any>(
    fn: F,
    options: SpanOptions & { name?: string } = {},
  ): F {
    // eslint-disable-next-line @typescript-eslint/no-this-alias -- the wrapper keeps the caller's own `this`
    const tracer = this;
    return function (this: unknown, ...args: Parameters<F>) {
      return tracer.withSpan(options.name ?? fn.name ?? 'span', options, () =>
        fn.apply(this, args),
      );
    } as F;
  }
  enqueue(span: WireSpan) {
    try {
      this.transport.enqueue(span);
    } catch {}
  }
  flush(timeout = 5000) {
    return this.transport.flush(timeout);
  }
  async shutdown(timeout = 5000) {
    if (this.stopped) return;
    this.stopped = true;
    process.off('beforeExit', this.beforeExit);
    try {
      await this.bridge?.shutdown();
    } catch {
      log('auto-capture shutdown failed');
    }
    await this.transport.shutdown(timeout);
  }
}

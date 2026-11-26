export type Kind = 'agent' | 'llm' | 'tool' | 'retrieval' | 'custom';
export type Status = 'ok' | 'error' | 'unset';
export interface WireSpan {
  trace_id: string;
  span_id: string;
  parent_span_id?: string | null;
  name: string;
  kind: Kind;
  started_at: string;
  ended_at?: string | null;
  status?: Status;
  input?: unknown;
  output?: unknown;
  input_truncated?: boolean;
  output_truncated?: boolean;
  model?: string;
  usage?: Record<string, number>;
  attributes?: Record<string, unknown>;
  error?: { type: string; message: string };
}
export interface SpanOptions {
  kind?: Kind;
  input?: unknown;
  model?: string;
  attributes?: Record<string, unknown>;
  parent?: import('./core.js').Span;
}
/** Structural OTel types keep the core free of required OTel imports. */
/* eslint-disable @typescript-eslint/no-explicit-any -- these mirror OTel's own signatures without importing them */
export interface SpanProcessorLike {
  onStart(span: any, parentContext?: any): void;
  onEnd(span: any): void;
  forceFlush(): Promise<void>;
  shutdown(): Promise<void>;
}
export interface TracerProviderLike {
  getTracer(name: string, version?: string, options?: any): any;
  addSpanProcessor?(processor: SpanProcessorLike): void;
}
/* eslint-enable @typescript-eslint/no-explicit-any */
export interface TracerOptions {
  apiKey?: string;
  baseUrl?: string;
  captureContent?: boolean;
  disabled?: boolean;
  redact?: (value: unknown, field: string, span: Readonly<WireSpan>) => unknown;
  serviceName?: string;
  autoCapture?: boolean | Array<'openai' | 'anthropic' | 'langchain'>;
  debug?: boolean;
  flushInterval?: number;
  timeout?: number;
  retryDelays?: number[];
  tracerProvider?: TracerProviderLike | ((processor: SpanProcessorLike) => TracerProviderLike);
  /** Explicit ESM module instances to patch. Use these when clients/frameworks are imported as ESM. */
  modules?: { openai?: unknown; anthropic?: unknown; langchain?: unknown };
}

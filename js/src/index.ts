export { version } from './version.js';
export { Tracer, Span } from './core.js';
export type {
  Kind,
  Status,
  SpanOptions,
  TracerOptions,
  WireSpan,
  SpanProcessorLike,
  TracerProviderLike,
} from './types.js';
import { Tracer, Span } from './core.js';
import type { TracerOptions, SpanOptions } from './types.js';
let current: Tracer | undefined;
export function init(options: TracerOptions = {}): Tracer {
  if (current) void current.shutdown();
  current = new Tracer(options);
  return current;
}
export function getTracer(): Tracer {
  return (current ??= new Tracer());
}
export function withSpan<T>(name: string, options: SpanOptions, fn: (span: Span) => T): T {
  return getTracer().withSpan(name, options, fn);
}
// eslint-disable-next-line @typescript-eslint/no-explicit-any -- standard idiom for wrapping any function signature
export function trace<F extends (...args: any[]) => any>(
  fn: F,
  options: SpanOptions & { name?: string } = {},
): F {
  return function (this: unknown, ...args: Parameters<F>) {
    return getTracer().withSpan(options.name ?? fn.name, options, () => fn.apply(this, args));
  } as F;
}
export function flush(timeout = 5000) {
  return getTracer().flush(timeout);
}
export async function shutdown(timeout = 5000) {
  await current?.shutdown(timeout);
}

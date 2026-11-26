import type { Tracer } from '../core.js';
import { CloudByteSpanExporter } from './exporter.js';
import { mapSpan } from './mapping.js';
export class CloudByteSpanProcessor {
  enabled = true;
  parents = new Map<string, string | undefined>();
  private exporter: CloudByteSpanExporter;
  constructor(private owner: Tracer) {
    this.exporter = new CloudByteSpanExporter(owner);
  }
  onStart(span: any) {
    if (!this.enabled) return;
    const parent = span.parentSpanContext ?? span.parentSpanId;
    this.parents.set(
      span.spanContext().spanId,
      typeof parent === 'string' ? parent : parent?.spanId,
    );
    if (!parent) {
      try {
        this.owner.enqueue(mapSpan(span, true));
      } catch {}
    }
  }
  onEnd(span: any) {
    if (this.enabled) {
      this.exporter.export([span], () => {});
      this.parents.delete(span.spanContext().spanId);
    }
  }
  async forceFlush() {
    await this.owner.flush();
  }
  async shutdown() {
    this.enabled = false;
    this.parents.clear();
  }
}

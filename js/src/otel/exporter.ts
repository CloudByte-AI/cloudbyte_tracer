import type { Tracer } from '../core.js';
import { mapSpan } from './mapping.js';
export class CloudByteSpanExporter {
  constructor(private owner: Tracer) {}
  export(spans: any[], callback: (result: { code: number }) => void) {
    for (const span of spans) {
      try {
        this.owner.enqueue(mapSpan(span));
      } catch {}
    }
    callback({ code: 0 });
  }
  async shutdown() {}
}

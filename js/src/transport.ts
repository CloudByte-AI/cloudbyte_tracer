import type { TracerOptions, WireSpan } from './types.js';
import { encode } from './limits.js';
import { version } from './version.js';
// Tells the server which SDK and runtime sent a batch, e.g. "cloudbyte-tracer-js/0.1.0 (node 24.11.0)".
export const USER_AGENT = `cloudbyte-tracer-js/${version} (node ${process.versions.node})`;
export const log = (message: string) => {
  try {
    console.error('cloudbyte_tracer: ' + message);
  } catch {}
};
export const retryAfter = (header: string | null): number =>
  header !== null && /^\d+$/.test(header.trim())
    ? Math.min(Number(header.trim()), 60) * 1000
    : 5000;
export class Transport {
  private queue: string[] = [];
  private bytes = 0;
  private timer?: ReturnType<typeof setTimeout>;
  private sending?: Promise<void>;
  private warned = false;
  private stopped = false;
  disabled = false;
  private abort?: AbortController;
  private retryTimer?: ReturnType<typeof setTimeout>;
  private wakeRetry?: () => void;
  /** Internal bounds exposed for deterministic offline tests. */
  maxQueueSpans = 10000;
  maxQueueBytes = 8000000;
  maxBatch = 500;
  maxBodyBytes = 4000000;
  constructor(readonly options: TracerOptions) {}
  enqueue(span: WireSpan) {
    if (this.disabled || this.stopped) return;
    const encoded = encode(span, this.options);
    if (!encoded) return;
    const size = Buffer.byteLength(encoded);
    if (size > this.maxQueueBytes || size + 12 > this.maxBodyBytes) {
      this.warnOverflow();
      return;
    }
    while (
      this.queue.length &&
      (this.queue.length >= this.maxQueueSpans || this.bytes + size > this.maxQueueBytes)
    ) {
      this.bytes -= Buffer.byteLength(this.queue.shift()!);
      this.warnOverflow();
    }
    this.queue.push(encoded);
    this.bytes += size;
    if (this.queue.length >= this.maxBatch) {
      void this.drain();
      return;
    }
    this.schedule();
  }
  private warnOverflow() {
    if (!this.warned) {
      this.warned = true;
      log('queue full; dropping oldest spans to keep tracing memory bounded');
    }
  }
  private schedule() {
    if (!this.timer && !this.sending && this.queue.length && !this.stopped) {
      this.timer = setTimeout(() => {
        this.timer = undefined;
        void this.drain();
      }, this.options.flushInterval ?? 1000);
      this.timer.unref();
    }
  }
  private drain(): Promise<void> {
    if (this.sending) return this.sending;
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = undefined;
    }
    this.sending = (async () => {
      while (this.queue.length && !this.stopped && !this.disabled) {
        const batch: string[] = [];
        let bytes = 12;
        while (this.queue.length && batch.length < this.maxBatch) {
          const item = this.queue[0],
            size = Buffer.byteLength(item) + 1;
          if (batch.length && bytes + size > this.maxBodyBytes) break;
          bytes += size;
          batch.push(this.queue.shift()!);
          this.bytes -= size - 1;
        }
        await this.send('{"spans":[' + batch.join(',') + ']}');
      }
    })()
      .catch(() => {
        log('sender failed; batch dropped');
      })
      .finally(() => {
        this.sending = undefined;
        this.schedule();
      });
    return this.sending;
  }
  private async send(body: string) {
    const delays = this.options.retryDelays ?? [500, 1000, 2000];
    for (let attempt = 0; attempt <= delays.length && !this.stopped; attempt++) {
      let delay = delays[attempt] ?? 0;
      this.abort = new AbortController();
      const timeout = setTimeout(() => this.abort?.abort(), this.options.timeout ?? 5000);
      try {
        const response = await fetch(this.options.baseUrl! + '/v1/agent-spans', {
          method: 'POST',
          redirect: 'error',
          headers: {
            Authorization: 'Bearer ' + this.options.apiKey,
            'Content-Type': 'application/json',
            'User-Agent': USER_AGENT,
          },
          body,
          signal: this.abort.signal,
        });
        if (response.status === 401 || response.status === 403) {
          this.disabled = true;
          this.queue = [];
          this.bytes = 0;
          log('HTTP ' + response.status + ': access denied; not sending further spans');
          await response.body?.cancel();
          return;
        }
        if (response.status >= 200 && response.status < 300) {
          if (this.options.debug) {
            try {
              const result = (await response.json()) as { rejected?: unknown[] };
              if (result.rejected?.length)
                log('server rejected ' + result.rejected.length + ' span(s)');
            } catch {}
          } else await response.body?.cancel();
          return;
        }
        await response.body?.cancel();
        if (response.status === 429) delay = retryAfter(response.headers.get('retry-after'));
        else if (response.status < 500) {
          if (this.options.debug) log('batch refused (HTTP ' + response.status + ')');
          return;
        }
      } catch {
        /* Network failures have the same bounded retry budget. */
      } finally {
        clearTimeout(timeout);
        this.abort = undefined;
      }
      if (attempt === delays.length || this.stopped) break;
      await new Promise<void>((resolve) => {
        this.wakeRetry = resolve;
        this.retryTimer = setTimeout(resolve, delay);
      });
      this.wakeRetry = undefined;
    }
    if (!this.stopped) log('retry budget exhausted; batch dropped');
  }
  async flush(timeout = 5000): Promise<boolean> {
    if (this.stopped || this.disabled) return true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const task = this.drain();
    const result = await Promise.race([
      task.then(() => true),
      new Promise<boolean>((resolve) => {
        timer = setTimeout(() => resolve(false), Math.max(0, timeout));
      }),
    ]);
    if (timer) clearTimeout(timer);
    if (!result && this.options.debug) log('flush timed out');
    return result;
  }
  async shutdown(timeout = 5000) {
    if (this.stopped) return;
    await this.flush(timeout);
    this.stopped = true;
    if (this.timer) clearTimeout(this.timer);
    if (this.retryTimer) clearTimeout(this.retryTimer);
    this.wakeRetry?.();
    this.abort?.abort();
    this.queue = [];
    this.bytes = 0;
  }
}

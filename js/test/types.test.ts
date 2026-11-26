import { test } from 'node:test';
import assert from 'node:assert/strict';
import { BasicTracerProvider } from '@opentelemetry/sdk-trace-base';
import type { TracerOptions } from '../src/index.js';
test('provider factory has contextual types without required OTel imports', () => {
  const options: TracerOptions = {
    tracerProvider: (processor) => new BasicTracerProvider({ spanProcessors: [processor] }),
  };
  assert.equal(typeof options.tracerProvider, 'function');
});

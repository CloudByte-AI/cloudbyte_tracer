import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { Tracer } from '../src/core.js';
import { encode } from '../src/limits.js';
import { retryAfter } from '../src/transport.js';
import { fakeServer, KEY, final } from './helpers.js';

test('nested spans send only root starts and preserve IDs', async () => {
  const s = await fakeServer(),
    t = new Tracer({ apiKey: KEY, baseUrl: s.url, flushInterval: 30000 });
  try {
    await t.withSpan('root', { kind: 'agent' }, async (root) => {
      await t.withSpan('child', { kind: 'tool' }, async (child) => {
        assert.equal(child.parentSpanId, root.spanId);
        assert.equal(child.traceId, root.traceId);
        assert.match(child.spanId, /^[a-f0-9]{16}$/);
        assert.match(child.traceId, /^[a-f0-9]{32}$/);
        child.setOutput({ ok: true });
      });
    });
    await t.flush();
    assert.equal(s.spans().length, 3);
    assert.deepEqual(final(s.spans(), 'child').output, { ok: true });
  } finally {
    await t.shutdown();
    await s.close();
  }
});
test('parallel promises keep separate parents', async () => {
  const s = await fakeServer(),
    t = new Tracer({ apiKey: KEY, baseUrl: s.url });
  try {
    await t.withSpan('root', {}, async () => {
      await Promise.all(
        [1, 2].map((n) =>
          t.withSpan('branch' + n, {}, async (parent) => {
            await new Promise((r) => setTimeout(r, 2));
            await t.withSpan('leaf' + n, {}, (leaf) =>
              assert.equal(leaf.parentSpanId, parent.spanId),
            );
          }),
        ),
      );
    });
    await t.flush();
    assert.notEqual(
      final(s.spans(), 'leaf1').parent_span_id,
      final(s.spans(), 'leaf2').parent_span_id,
    );
  } finally {
    await t.shutdown();
    await s.close();
  }
});
test('decorators preserve receiver, return values and app errors', async () => {
  const s = await fakeServer(),
    t = new Tracer({ apiKey: KEY, baseUrl: s.url });
  try {
    const obj = {
      value: 42,
      run: t.trace(
        function (this: { value: number }) {
          return this.value;
        },
        { kind: 'tool' },
      ),
    };
    assert.equal(obj.run(), 42);
    const error = Error('app error');
    await assert.rejects(
      t.withSpan('async', {}, async () => {
        throw error;
      }),
      (e) => e === error,
    );
    assert.throws(
      () =>
        t.withSpan('sync', {}, () => {
          throw error;
        }),
      (e) => e === error,
    );
    await t.flush();
    assert.equal(final(s.spans(), 'async').status, 'error');
  } finally {
    await t.shutdown();
    await s.close();
  }
});
test('content redaction fails closed and limits attributes/errors/tokens', async () => {
  const s = await fakeServer(),
    t = new Tracer({
      apiKey: KEY,
      baseUrl: s.url,
      redact: (v, field) => {
        if (field === 'output') throw Error('private');
        return typeof v === 'string' && v === 'secret' ? 'redacted' : v;
      },
    });
  try {
    t.withSpan(
      'root',
      { input: 'secret', attributes: { small: 'yes', large: 'x'.repeat(40000), secret: 'secret' } },
      (span) => {
        span.setOutput('secret');
        span.setUsage({ input_tokens: 9999999999999, output_tokens: -1 });
        span.recordError(Error('😀'.repeat(5000)));
      },
    );
    await t.flush();
    const span = final(s.spans(), 'root');
    assert.equal(span.input, 'redacted');
    assert.equal(span.output, undefined);
    assert.equal(span.attributes?.secret, 'redacted');
    assert.equal(span.attributes?.large, undefined);
    assert.deepEqual(span.usage, { input_tokens: 2147483647 });
    assert.equal(Array.from(span.error!.message).length, 4096);
  } finally {
    await t.shutdown();
    await s.close();
  }
});
test('captureContent false omits content', async () => {
  const s = await fakeServer(),
    t = new Tracer({ apiKey: KEY, baseUrl: s.url, captureContent: false });
  try {
    t.withSpan('root', { input: 'secret' }, (span) => span.setOutput('secret'));
    await t.flush();
    assert.ok(!JSON.stringify(s.spans()).includes('secret'));
  } finally {
    await t.shutdown();
    await s.close();
  }
});
for (const code of [401, 403])
  test(code + ' permanently disables sending', async () => {
    const s = await fakeServer();
    s.setStatus(code);
    const t = new Tracer({ apiKey: KEY, baseUrl: s.url });
    try {
      t.withSpan('root', {}, () => {});
      await t.flush();
      t.withSpan('later', {}, () => {});
      await t.flush();
      assert.equal(s.bodies.length, 1);
      assert.equal(t.transport.disabled, true);
    } finally {
      await t.shutdown();
      await s.close();
    }
  });
for (const code of [400, 413, 429, 500])
  test(code + ' follows bounded retry rules', async () => {
    const s = await fakeServer();
    s.setStatus(code);
    const t = new Tracer({ apiKey: KEY, baseUrl: s.url, retryDelays: [0, 0] });
    try {
      t.withSpan('root', {}, () => {});
      await t.flush();
      assert.equal(s.bodies.length, code >= 429 ? 3 : 1);
    } finally {
      await t.shutdown();
      await s.close();
    }
  });
test('Retry-After defaults and caps', () => {
  assert.equal(retryAfter('999'), 60000);
  assert.equal(retryAfter('0'), 0);
  for (const v of [null, 'later', '-1', '1.5']) assert.equal(retryAfter(v), 5000);
});
test('queue drops oldest with count and byte limits and flushes bounded batches', async () => {
  const s = await fakeServer(),
    t = new Tracer({ apiKey: KEY, baseUrl: s.url, flushInterval: 30000 });
  try {
    t.transport.maxQueueSpans = 3;
    for (let i = 0; i < 4; i++) t.withSpan('root' + i, {}, () => {});
    await t.flush();
    // Eviction takes the oldest ordinary copies (here, start copies) first, so the end copies that close
    // the newest runs survive: root1, root2 and root3 each keep a complete copy.
    assert.equal(s.spans().length, 3);
    assert.deepEqual(
      s.spans().map((span) => span.name),
      ['root1', 'root2', 'root3'],
    );
    t.transport.maxQueueSpans = 10000;
    t.transport.maxQueueBytes = 1500;
    for (let i = 0; i < 10; i++) t.withSpan('large' + i, { input: 'x'.repeat(400) }, () => {});
    await t.flush();
    assert.ok(s.bodies.at(-1)!.length < 20);
  } finally {
    await t.shutdown();
    await s.close();
  }
});
test('root end copy is sent in the first batch', async () => {
  const s = await fakeServer(),
    t = new Tracer({ apiKey: KEY, baseUrl: s.url, flushInterval: 30000 });
  try {
    // A large maxBatch while tracing keeps the sender idle, so every copy is queued before the first batch.
    t.transport.maxBatch = 1000;
    t.withSpan('job', { kind: 'agent' }, () => {
      for (let i = 0; i < 250; i++) t.withSpan('step-' + i, { kind: 'tool' }, () => {});
    });
    t.transport.maxBatch = 100;
    await t.flush();
    assert.equal(s.bodies[0].length, 100);
    assert.equal(s.bodies[0][0].name, 'job');
    assert.ok(s.bodies[0][0].ended_at);
    assert.equal(s.spans().length, 252); // 250 steps, plus the root's start and end copies
  } finally {
    await t.shutdown();
    await s.close();
  }
});
test('overflow evicts children before root end copies', async () => {
  const s = await fakeServer(),
    t = new Tracer({ apiKey: KEY, baseUrl: s.url, flushInterval: 30000 });
  try {
    t.transport.maxQueueSpans = 5;
    t.withSpan('job', { kind: 'agent' }, () => {
      for (let i = 0; i < 6; i++) t.withSpan('step-' + i, { kind: 'tool' }, () => {});
    });
    await t.flush();
    // 8 copies for 5 places: the 3 oldest ordinary copies went (the start copy, step-0, step-1).
    assert.deepEqual(
      s.spans().map((span) => span.name),
      ['job', 'step-2', 'step-3', 'step-4', 'step-5'],
    );
    assert.ok(s.spans()[0].ended_at);
  } finally {
    await t.shutdown();
    await s.close();
  }
});
test('root start copy keeps FIFO order', async () => {
  const s = await fakeServer(),
    t = new Tracer({ apiKey: KEY, baseUrl: s.url, flushInterval: 30000 });
  try {
    t.withSpan('job', { kind: 'agent' }, () => t.withSpan('child', { kind: 'tool' }, () => {}));
    await t.flush();
    // The end copy jumps the queue; the start copy keeps its place ahead of the child.
    assert.deepEqual(
      s.spans().map((span) => [span.name, Boolean(span.ended_at)]),
      [
        ['job', true],
        ['job', false],
        ['child', true],
      ],
    );
  } finally {
    await t.shutdown();
    await s.close();
  }
});
test('500 copy batches, authorization, and byte budget', async () => {
  const s = await fakeServer(),
    t = new Tracer({ apiKey: KEY, baseUrl: s.url, flushInterval: 30000 });
  try {
    for (let i = 0; i < 600; i++) t.withSpan('root' + i, {}, () => {});
    await t.flush();
    assert.equal(s.spans().length, 1200);
    assert.ok(
      s.bodies.every(
        (b) => b.length <= 500 && Buffer.byteLength(JSON.stringify({ spans: b })) < 5000000,
      ),
    );
    assert.equal(s.headers[0].authorization, 'Bearer ' + KEY);
    // Source tests run unbuilt, so the version is 'development'; the build injects package.json's.
    assert.equal(
      s.headers[0]['user-agent'],
      `cloudbyte-tracer-js/development (node ${process.versions.node})`,
    );
  } finally {
    await t.shutdown();
    await s.close();
  }
});
test('large unicode content is bounded without splitting codepoints', async () => {
  const s = await fakeServer(),
    t = new Tracer({ apiKey: KEY, baseUrl: s.url });
  try {
    t.withSpan('root', { input: '😀'.repeat(100000) }, () => {});
    await t.flush();
    const span = final(s.spans(), 'root');
    assert.ok(Buffer.byteLength(span.input as string) <= 70000);
    assert.equal(span.input_truncated, true);
    assert.ok(!(span.input as string).includes('�'));
  } finally {
    await t.shutdown();
    await s.close();
  }
});
test('network failures and flush timeout do not reach app', async () => {
  const t = new Tracer({ apiKey: KEY, baseUrl: 'http://127.0.0.1:1', retryDelays: [30000] });
  t.withSpan('root', {}, () => {});
  assert.equal(await t.flush(1), false);
  await t.shutdown(1);
});
test('interval sends partial batch and shutdown is idempotent', async () => {
  const s = await fakeServer(),
    t = new Tracer({ apiKey: KEY, baseUrl: s.url, flushInterval: 2 });
  try {
    t.withSpan('root', {}, () => {});
    await new Promise((r) => setTimeout(r, 100));
    assert.equal(s.spans().length, 2);
    await t.shutdown();
    await t.shutdown();
    t.withSpan('ignored', {}, () => {});
    assert.equal(s.spans().length, 2);
  } finally {
    await t.shutdown();
    await s.close();
  }
});
test('missing key is a no-op', async () => {
  const t = new Tracer({ disabled: true });
  assert.equal(
    t.withSpan('root', {}, () => 42),
    42,
  );
  assert.equal(t.transport.disabled, true);
  await t.shutdown();
});
test('contract fixtures match JS encoder', () => {
  const fixture = JSON.parse(
    readFileSync(new URL('../../contract/fixtures.json', import.meta.url), 'utf8'),
  );
  for (const c of fixture.cases) assert.deepEqual(JSON.parse(encode(c.raw, {})!), c.wire);
});

test('beforeExit flushes without explicit shutdown', async () => {
  const { spawn } = await import('node:child_process');
  const s = await fakeServer();
  try {
    const code =
      'import {Tracer} from ' +
      JSON.stringify(new URL('../src/index.ts', import.meta.url).href) +
      ';const t=new Tracer({apiKey:' +
      JSON.stringify(KEY) +
      ',baseUrl:' +
      JSON.stringify(s.url) +
      ",flushInterval:30000});t.withSpan('exit',{},()=>{});";
    const child = spawn(process.execPath, ['--import', 'tsx', '--input-type=module', '-e', code], {
      cwd: process.cwd(),
      stdio: 'pipe',
    });
    let stderr = '';
    child.stderr.on('data', (b) => (stderr += b));
    const timeout = setTimeout(() => child.kill(), 15000);
    const codeResult = await new Promise<number | null>((resolve) => child.on('close', resolve));
    clearTimeout(timeout);
    assert.equal(codeResult, 0, stderr);
    assert.equal(s.spans().length, 2);
  } finally {
    await s.close();
  }
});
test('thrown undefined propagates and is recorded as error', async () => {
  const s = await fakeServer(),
    t = new Tracer({ apiKey: KEY, baseUrl: s.url });
  try {
    let reached = false;
    try {
      t.withSpan('throw', {}, () => {
        throw undefined;
      });
    } catch {
      reached = true;
    }
    assert.ok(reached);
    await t.flush();
    assert.equal(final(s.spans(), 'throw').status, 'error');
  } finally {
    await t.shutdown();
    await s.close();
  }
});

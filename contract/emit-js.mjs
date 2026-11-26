import { readFileSync, writeFileSync } from 'node:fs';
import { Tracer } from '../js/dist/index.js';
const t = new Tracer({ apiKey: 'cbagt_' + 'T'.repeat(32) }),
  spans = [];
t.enqueue = (span) => spans.push(JSON.parse(JSON.stringify(span)));
await t.withSpan('runtime-root', { kind: 'agent', input: { question: 'hi' } }, async () => {
  await t.withSpan('runtime-tool', { kind: 'tool' }, (span) => span.setOutput({ answer: 'hello' }));
});
await t.shutdown();
writeFileSync(process.argv[2], JSON.stringify({ spans }), { encoding: 'utf8' });

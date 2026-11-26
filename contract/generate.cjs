// Read-only use of the backend's pure modules. Never imports the app or database.
const fs = require('node:fs'),
  path = require('node:path'),
  assert = require('node:assert/strict');
const [backend, pythonPayload, jsPayload] = process.argv.slice(2);
if (!backend || !pythonPayload || !jsPayload)
  throw Error(
    'Usage: node contract/generate.cjs <backend-dir> <python-spans.json> <js-spans.json>',
  );
const { normalizeSpan, validateBatch } = require(
  path.resolve(backend, 'server/lib/agent-observability/validate.js'),
);
const { mergeSpan } = require(path.resolve(backend, 'server/lib/agent-observability/merge.js'));
const cases = JSON.parse(fs.readFileSync(path.join(__dirname, 'cases.json'), 'utf8'));
const python = JSON.parse(fs.readFileSync(pythonPayload, 'utf8'));
const fixture = {
  generator: 'backend pure normalizeSpan + mergeSpan; no app/DB imports',
  cases: [],
};
for (let i = 0; i < cases.length; i++) {
  const wire = python.spans[i],
    normalized = normalizeSpan(wire);
  assert.equal(normalized.ok, true, normalized.error);
  fixture.cases.push({ ...cases[i], wire, normalized: normalized.span });
}
for (const file of [pythonPayload, jsPayload]) {
  const payload = JSON.parse(fs.readFileSync(file, 'utf8')),
    stored = new Map();
  for (let i = 0; i < payload.spans.length; i += 500)
    validateBatch({ spans: payload.spans.slice(i, i + 500) });
  for (const wire of payload.spans) {
    const norm = normalizeSpan(wire);
    assert.equal(norm.ok, true, norm.error);
    const merged = mergeSpan(stored.get(wire.span_id), norm.span);
    assert.equal(merged.ok, true, merged.error);
    stored.set(wire.span_id, merged.row);
  }
  console.log(
    path.basename(file) + ': ' + payload.spans.length + ' SDK copies validated and merged',
  );
}
const start = fixture.cases[0].normalized,
  end = fixture.cases[1].normalized;
const merged = mergeSpan(mergeSpan(null, start).row, end);
assert.equal(merged.ok, true);
assert.deepEqual(merged.row.input, start.input);
assert.equal(merged.row.duration_ms, 1000);
assert.equal(fixture.cases[3].normalized.input_truncated, true);
fixture.merge = merged.row;
fs.writeFileSync(
  path.join(__dirname, 'fixtures.json'),
  JSON.stringify(fixture, null, 2) + '\n',
  'utf8',
);
console.log('Wrote contract/fixtures.json (' + fixture.cases.length + ' cases)');

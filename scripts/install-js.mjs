import { mkdirSync, writeFileSync, readFileSync, cpSync } from 'node:fs';
import { resolve, dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync, execFileSync, spawn } from 'node:child_process';
import { createServer } from 'node:http';
import assert from 'node:assert/strict';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..'),
  tarball = resolve(process.argv[2]),
  dir = join(
    root,
    'artifacts',
    'install-js-' + process.versions.node.split('.')[0] + (process.argv[3] ?? ''),
  );
mkdirSync(dir);
writeFileSync(join(dir, 'package.json'), JSON.stringify({ private: true, type: 'module' }));
const npm = 'npm';
const npmCli =
  process.platform === 'win32'
    ? (process.env.npm_execpath ??
      join(
        dirname(
          execFileSync('where.exe', ['npm.cmd'], { encoding: 'utf8' }).trim().split(/\r?\n/)[0],
        ),
        'node_modules/npm/bin/npm-cli.js',
      ))
    : undefined;
function run(cmd, args, opts = {}) {
  const result = spawnSync(
    npmCli && cmd === npm ? process.execPath : cmd,
    npmCli && cmd === npm ? [npmCli, ...args] : args,
    { cwd: dir, stdio: 'inherit', ...opts },
  );
  if (result.error) throw result.error;
  if (result.status !== 0) throw Error(cmd + ' failed: ' + result.status);
}
run(npm, ['install', '--ignore-scripts', '--omit=optional', tarball]);
run(process.execPath, [
  '--input-type=module',
  '-e',
  "import {Tracer,version} from 'cloudbyte-tracer'; if(version!=='0.1.0') throw Error(version); const t=new Tracer({disabled:true}); t.withSpan('core',{},()=>42); await t.shutdown(); console.log('ESM core import passed')",
]);
run(process.execPath, [
  '-e',
  "const {Tracer,version}=require('cloudbyte-tracer'); if(version!=='0.1.0') throw Error(version); const t=new Tracer({disabled:true}); t.withSpan('core',{},()=>42); t.shutdown().then(()=>console.log('CJS core import passed'))",
]);
cpSync(join(root, 'examples/js'), join(dir, 'examples'), {
  recursive: true,
  filter: (src) =>
    !src.includes('node_modules') &&
    !src.endsWith('package.json') &&
    !src.endsWith('package-lock.json'),
});
const env = Object.fromEntries(
  Object.entries(process.env).filter(([k]) => !k.startsWith('CLOUDBYTE_')),
);
env.CLOUDBYTE_DISABLED = 'true';
run(process.execPath, ['examples/manual.mjs'], { env });
const pkg = JSON.parse(readFileSync(join(root, 'js/package.json'), 'utf8'));
const deps = Object.entries(pkg.devDependencies)
  .filter(([n]) => !['tsx', 'tsup', 'typescript', '@types/node'].includes(n))
  .map(([n, v]) => n + '@' + v);
run(npm, ['install', '--ignore-scripts', ...deps]);
const spans = [];
const server = createServer((req, res) => {
  const chunks = [];
  req.on('data', (chunk) => chunks.push(chunk));
  req.on('end', () => {
    const body = JSON.parse(Buffer.concat(chunks));
    spans.push(...body.spans);
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ accepted: body.spans.length, rejected: [] }));
  });
});
await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
env.CLOUDBYTE_DISABLED = 'false';
env.CLOUDBYTE_AGENT_KEY = 'cbagt_' + 'T'.repeat(32);
env.CLOUDBYTE_BASE_URL = 'http://127.0.0.1:' + server.address().port;
try {
  for (const name of ['openai', 'anthropic', 'langgraph']) {
    spans.length = 0;
    await new Promise((resolve, reject) => {
      const child = spawn(process.execPath, ['examples/' + name + '.mjs'], {
        env,
        cwd: dir,
        stdio: 'inherit',
      });
      child.on('error', reject);
      child.on('close', (code) =>
        code === 0 ? resolve() : reject(Error(name + ' exited ' + code)),
      );
    });
    const llm = spans.find((s) => s.kind === 'llm' && s.ended_at);
    assert.ok(llm, name);
    assert.equal(llm.model, 'test-model');
    assert.equal(llm.usage.input_tokens, 7);
    const parent = spans.find((s) => s.span_id === llm.parent_span_id);
    assert.ok(parent);
    if (name === 'langgraph') assert.equal(parent.name, 'respond');
  }
} finally {
  await new Promise((resolve) => {
    server.close(resolve);
    server.closeAllConnections();
  });
}
console.log('Clean npm tarball install, ESM/CJS, and four offline examples passed');

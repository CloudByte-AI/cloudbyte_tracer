import { defineConfig } from 'tsup';
import { readFileSync } from 'node:fs';
const pkg = JSON.parse(readFileSync('package.json', 'utf8'));
export default defineConfig({
  entry: ['src/index.ts'],
  format: ['esm', 'cjs'],
  dts: true,
  clean: true,
  target: 'node20',
  define: { __SDK_VERSION__: JSON.stringify(pkg.version) },
  esbuildOptions(options, context) {
    if (context.format === 'cjs')
      options.define = { ...options.define, 'import.meta.url': '__filename' };
  },
});

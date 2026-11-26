// Lint rules for the JS SDK package: source, tests and config files.
// Formatting is Prettier's job (npm run format); this file only catches mistakes.
import js from '@eslint/js';
import globals from 'globals';
import tseslint from 'typescript-eslint';

export default tseslint.config(
  { ignores: ['dist/', 'node_modules/'] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    languageOptions: { globals: globals.node },
    rules: {
      // Unused values are mistakes; a leading underscore marks a deliberate one.
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_', caughtErrors: 'none' },
      ],
      // The SDK must never throw into the host app, so empty catch blocks are intentional.
      'no-empty': ['error', { allowEmptyCatch: true }],
    },
  },
  {
    // The auto-capture bridge talks to optional peer packages (OpenTelemetry, OpenInference)
    // whose types the core must not import, and tests poke at internals. `any` is allowed here
    // and nowhere else.
    files: ['src/otel/**/*.ts', 'test/**/*.ts'],
    rules: { '@typescript-eslint/no-explicit-any': 'off' },
  },
);

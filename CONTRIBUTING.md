# Development

Use Python 3.10+ and Node 20+. Create/activate a venv, then pip install -e './python[all,test]'. Run python -m pytest python/tests -q. In js/, npm ci --ignore-scripts, npm run format:check, npm run lint, npm run typecheck, npm test and npm run build.

JS formatting is Prettier (config in .prettierrc.json at the repo root; run npm run format in js/ to apply it to the SDK, tests, examples and JS scripts). Linting is ESLint with typescript-eslint (js/eslint.config.js). The SDK never throws into the host app, so empty catch blocks are allowed. Avoid any in the core; it is allowed only in src/otel/ and tests, where code talks to optional OpenTelemetry/OpenInference peers whose types the core must not import. Any other any needs a disable comment that says why. CI fails on unformatted or lint-failing code. Build Python with python -m build python --outdir artifacts/python; in js/ run npm pack --pack-destination ../artifacts.

Version manifests together. Python reads installed metadata; JS injects package.json version at build. CI tests Python 3.10-3.14 and Node 20/22/24 and installs artifacts in clean projects. Provider tests/examples use mock HTTP responses; no live API keys.

## Contract fixtures

Both languages assert encoder output against checked-in contract/fixtures.json. To regenerate, build JS, create artifacts/, run python contract/emit-python.py artifacts/python-spans.json, node contract/emit-js.mjs artifacts/js-spans.json, then node contract/generate.cjs /path/to/claude-mem-central artifacts/python-spans.json artifacts/js-spans.json. Only backend pure validate/merge modules are loaded; no app, SQL or DB. CI uses checked-in fixtures without access to PMS. Review fixture diffs after contract changes.

## Distribution and releases

Both packages are published publicly: `cloudbyte-tracer` on PyPI and on npm. Runtime code contains no registry URLs or update checks.

To release:

1. Bump `version` in `python/pyproject.toml` and `js/package.json` to the same value, and merge to `main`.
2. Push the tag `v<version>` (only maintainers can; see the tag ruleset). `scripts/check-version.py` fails the run if the tag and both versions don't match.
3. `release.yml` builds, tests and installs both packages, then waits for a release manager to approve the `pypi` and `npm` environments.
4. PyPI publishes on approval. npm stages the release (`npm stage publish`); an npm owner approves it on npmjs.com under Staged Packages, with two-factor login.
5. The workflow creates the GitHub Release with the wheel, sdist and npm tarball attached.

A manual run of `release.yml` only builds and tests. Tick "rehearse on TestPyPI" to also upload the Python package to TestPyPI (needs the `testpypi` environment and a TestPyPI pending publisher).

Trusted publishing (OIDC) is used throughout: no publishing tokens exist anywhere. PyPI and npm trust `CloudByte-AI/cloudbyte_tracer`, workflow `release.yml`, environments `pypi` and `npm`; those names must not change without updating both registries. npm also requires `repository.url` in `js/package.json` to match the GitHub repository exactly.

The `0.0.1` npm placeholder that claimed the name lives in `release/npm-placeholder/`; deprecate it once `0.1.0` is out (`npm deprecate cloudbyte-tracer@0.0.1 "Placeholder; install 0.1.0 or later"`).

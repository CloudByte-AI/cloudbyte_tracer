# Release helpers

## npm-placeholder

npm can only connect trusted publishing to a package that already exists, so an npm owner publishes this
placeholder once by hand to claim the name `cloudbyte-tracer`. It contains a README and no code.

1. Check `npm -v` shows 11.5.1 or later, and that the npm account has two-factor login.
2. From this folder (`release/npm-placeholder`): run `npm pack --dry-run` and check the only files listed
   are `package.json` and `README.md`.
3. Run `npm login`, then `npm publish --access public`, and approve with the two-factor code.
4. Add the other owners: `npm owner add <their-npm-username> cloudbyte-tracer`.

Once `0.1.0` is released through `release.yml`, deprecate the placeholder:

```
npm deprecate cloudbyte-tracer@0.0.1 "Placeholder; install 0.1.0 or later"
```

PyPI needs no placeholder: the PyPI organisation registers a pending trusted publisher before the first upload.

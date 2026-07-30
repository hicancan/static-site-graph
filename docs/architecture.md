# Architecture

The complete production transformation is:

```text
SiteDefinition
  → fetch
  → discover
  → classify
  → extract
  → SitePackage
```

`SiteDefinition` and `SitePackage` are owned by this repository. HTTP retry is
the only retry behavior and repeats the same request without changing
classification or extraction semantics.

The implementation remains compact:

- `model.py` owns the two boundary objects and current package identity.
- `fetch.py` owns HTTP results.
- homepage/section/page crawl modules own discovery and traversal.
- `classify.py` and `extract.py` each own one production semantic.
- `package.py` writes and validates the single current package format.
- `plugin.py` defines the callable used by instance-owned special adapters.

An instance repository owns site configurations and plugins. It consumes
`SitePackage`; this repository never imports or invokes an instance.

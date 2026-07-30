# static-site-graph

`static-site-graph` is the generic producer of `SitePackage` artifacts for
static and semi-static websites.

Its production flow is intentionally one-way:

```text
SiteDefinition
  → fetch
  → discover
  → classify
  → extract
  → SitePackage
```

The repository owns two boundary objects:

- `SiteDefinition`: one site's identity and crawl configuration.
- `SitePackage`: discovered navigation, sections, list/detail pages,
  attachment metadata, links, edges, and factual crawl diagnostics. Its
  manifest identifies the whole package and verifies every member by size and
  SHA-256.

It does not contain site-instance adapters, search logic, completeness
governance, or data for a particular organization. Network retries may repeat
the same request; they do not alter extraction semantics.

## Install and test

```powershell
uv venv
uv pip install -e ".[dev]"
uv run pytest -q
uv run python -m sitegraph.cli validate-config examples/sites/demo/site.yaml
uv run python -m sitegraph.cli crawl-site examples/sites/demo/site.yaml --dry-run
```

## Produce a SitePackage

```powershell
uv run python -m sitegraph.cli crawl-site path\to\site.yaml --out D:\Data\site-package
```

Incremental crawling reuses an explicitly supplied current package:

```powershell
uv run python -m sitegraph.cli crawl-site path\to\site.yaml --out D:\Data\site-package --incremental
```

The package manifest contains the current format, start/end times, record
counts, and visible HTTP/parse/classification diagnostics. Generated packages
are disposable artifacts and should live outside source directories.

Site-specific APIs or browser behavior belong in the consuming instance
repository. Such a repository can implement the callable interface in
`sitegraph.plugin` and still emit the producer-owned `SitePackage`.

Cross-repository local orchestration must pass this repository path explicitly;
the generic crawler never locates or invokes an instance or search repository.

## Repository layout

- `src/sitegraph`: generic fetch/discovery/classification/extraction/package code
- `src/sitegraph/package_contract`: the producer-owned current `SitePackage` schemas
- `tests`: implementation-adjacent behavior and contract tests
- `examples`: generic fixtures only

Licensed under AGPL-3.0.

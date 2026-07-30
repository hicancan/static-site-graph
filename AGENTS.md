# static-site-graph agent instructions

Own only the generic `SiteDefinition → SitePackage` mechanism.

- No organization-specific adapters or production data.
- One current package format; incompatible inputs fail.
- Diagnostics are factual crawl results, not governance artifacts.
- Generated data is disposable and does not belong in source architecture.
- Preserve one implementation for discovery, classification, and extraction.
- Tests protect real crawl and package behavior, not old internal structure.

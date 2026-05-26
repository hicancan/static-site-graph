# Data contract

The framework emits a stable site package under `data/sites/<site_id>/index/`.

## Required files

- `site.json`: site-level identity and crawl metadata.
- `nav_tree.json`: tree of navigation nodes.
- `sections.json`: smallest crawlable physical website sections.
- `list_pages.jsonl`: list and pagination pages.
- `detail_pages.jsonl`: content pages.
- `attachments.jsonl`: attachment metadata only, no binaries by default.
- `external_links.jsonl`: external systems, external policy links, cross-domain articles.
- `edges.jsonl`: graph edges among all URL-bearing objects.
- `manifest.json`: crawl totals, statuses, stop reasons, failures, quality counters.
- `audit_report.md`: human-readable audit summary.

## Provenance principle

Every derived object must carry enough provenance to answer:

- where was it discovered?
- what was fetched?
- how was it extracted?
- when was it fetched?
- what failed or was skipped?

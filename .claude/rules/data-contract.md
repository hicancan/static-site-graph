# Data contract rules

The core output contract is:

- `site.json`
- `nav_tree.json`
- `sections.json`
- `list_pages.jsonl`
- `detail_pages.jsonl`
- `attachments.jsonl`
- `external_links.jsonl`
- `edges.jsonl`
- `manifest.json`
- `audit_report.md`

Any new field must be documented in `docs/data-contract.md` and represented in `schemas/`.

Generated records must include enough provenance to reproduce them:

- source URL;
- parent URL or parent ID;
- discovered_at/fetched_at;
- extraction strategy;
- status;
- content hash when content exists.

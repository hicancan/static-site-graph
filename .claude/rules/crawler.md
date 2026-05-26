---
paths:
  - "src/sitegraph/crawl/**/*.py"
  - "src/sitegraph/extract/**/*.py"
  - "src/sitegraph/classify/**/*.py"
---

# Crawler rules

- Crawlers are configuration-driven.
- Discovery crawlers may be broad, but production crawlers must use explicit site/section config.
- Every URL must end in exactly one outcome: crawled, skipped, failed, external, attachment, duplicate, out_of_scope, or unknown.
- Pagination must record stop reason: no_next_link, duplicate_next_url, no_new_items, max_pages_safety, http_error, parse_error.
- Do not use business filters in the raw crawl phase.
- List extraction and detail extraction are separate functions.
- Direct attachment list items are valid first-class resources.

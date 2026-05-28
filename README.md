# static-site-graph

`static-site-graph` is a Claude-Code-first template project for turning static or semi-static content websites into structured, auditable site graphs and crawlable mirror indexes.

It is **not** a search engine and not a site-specific crawler. It is the upstream framework that produces a structured truth source for downstream products such as `njupt-search`.

## Core idea

A site is modeled as:

```text
site
  -> navigation tree
  -> sections / columns
  -> list pages
  -> pagination pages
  -> detail pages
  -> attachment metadata
  -> external links / systems
  -> edge graph
  -> manifest + audit report
```

## Intended workflow

1. Explore a reference site with Claude Code + Chrome.
2. Encode the site model in config files.
3. Run configuration-driven crawlers.
4. Export a structured mirror index.
5. Feed downstream systems.
6. Backfeed reusable discoveries into this template.

## Repository boundary

Only framework source, schemas, examples, tests, workflows, and the minimal README are tracked here.
Local docs, audit notes, prompts, and agent/Claude/Codex configuration are intentionally ignored so the framework stays product-facing and reusable.

## Development commands

```bash
python -m pip install -e .[dev]
python -m sitegraph.cli --help
pytest
```

## Non-negotiable design rules

- Discovery, modeling, crawling, validation, and export are separate phases.
- Raw URL discovery must not silently drop URLs.
- Every skipped, failed, external, attachment, unknown, or low-evidence page must be recorded.
- Page type classification is mandatory before extraction.
- Chrome is mandatory during modeling for representative pages and when browser DOM differs from HTTP fetch output.
- Instance-specific rules must stay in instance configs; do not hard-code JWC or NJUPT into the framework core.

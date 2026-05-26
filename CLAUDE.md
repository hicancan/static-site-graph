# Claude Code instructions for static-site-graph

You are working in the `static-site-graph` template framework.

## Mission

Build and maintain a reusable framework for converting static or semi-static websites into structured site graphs and crawlable mirror indexes.

## Required workflow

For every meaningful change:

1. Read this file and relevant `.claude/rules/*.md` files.
2. Determine whether the change belongs in the template or in an instance project.
3. Preserve the data contract in `schemas/` unless the task explicitly changes it.
4. If the data contract changes, update schemas, docs, tests, and examples together.
5. Run the relevant validation command and report exact results.
6. If a pattern comes from an instance project and is reusable, generalize it here without hard-coding the instance.

## Hard constraints

- Never hide failures. Emit manifest events instead.
- Never conflate business channels with physical website sections.
- Never assume all list items are detail pages. Items may be direct attachments or external links.
- Never treat HTTP 200 as extraction success. Track title/body/date/attachment extraction independently.
- Do not save attachment binaries by default; keep URL, name, type, and parent page context.
- Do not add secrets or `.env` files.

## Useful commands

```bash
python -m pip install -e .[dev]
python -m sitegraph.cli validate-config examples/sites/jwc/config/site.yaml
python -m sitegraph.cli crawl-site examples/sites/jwc/config/site.yaml --dry-run
pytest
```

## Chrome policy

Use Chrome during modeling, not as the default bulk crawler. Use it when:

- the site has hover menus, hidden navigation, dynamic content, login/CAPTCHA, browser-only DOM, or abnormal HTTP behavior;
- a selector returns empty or low-evidence content;
- an HTTP snapshot differs from visible DOM;
- a new site type, page family, or pagination pattern is introduced.

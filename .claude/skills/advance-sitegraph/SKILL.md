---
description: Advance a static-site-graph template or instance project toward complete structured site modeling, crawl, validation, and export readiness.
argument-hint: "[site-id or goal]"
disable-model-invocation: true
allowed-tools: Read Grep Glob Bash Edit Write WebFetch
---

Execute this workflow:

1. Read `CLAUDE.md`, `.claude/rules/`, `docs/architecture.md`, `docs/data-contract.md`, and the relevant site config.
2. Identify whether the work belongs to framework core or site instance config.
3. Inspect existing schemas and validation tests before changing output contracts.
4. For site modeling tasks, ensure every discovered URL is classified and every failure is manifested.
5. Use Chrome when the task involves real website DOM, menus, pagination, low-content pages, or selector uncertainty.
6. Run validation and tests.
7. If a reusable pattern emerges from an instance, update the template docs/schemas/code without hard-coding the instance.
8. Report changed files, validation commands, and remaining gaps.

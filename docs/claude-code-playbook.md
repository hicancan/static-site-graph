# Claude Code playbook

This project uses Claude Code as the only expected AI coding tool.

## Project memory

Use `CLAUDE.md` for stable project instructions and `.claude/rules/` for modular rules. Keep `CLAUDE.md` concise and procedural. Put long task workflows into skills.

## Skills

Project skills live at `.claude/skills/<name>/SKILL.md` and can be invoked with `/<name>`. Use skills for repeatable workflows that should not be loaded into context on every turn.

## `/goal`

Use `/goal` for substantial work with a verifiable end state. The condition must include explicit proof commands and a stopping bound.

Good condition shape:

```text
/goal The sitegraph package for site X is complete: config validates, crawl smoke test exits 0, generated artifacts satisfy schemas, manifest has no silent drops, audit_report.md lists all remaining manual-Chrome items; prove this by surfacing exact command outputs, or stop after 20 turns with a blocker list.
```

## Chrome

Start Claude with:

```bash
claude --chrome
```

Use `/chrome` to verify browser connection and permissions. Browser inspection is mandatory for initial site modeling and selector uncertainty.

<!--
Append this section to the project's CLAUDE.md.
Placeholders to substitute: <VAULT_PATH>, <BRANCH>, <WS_PATH>
-->

## Vault context loading (via workingset)

This project has a markdown vault at `<VAULT_PATH>` that is managed by
[workingset](https://github.com/idanshimon/workingset). When loading
context from any branch under that vault, follow these rules:

### Always read the brief first

For any branch `<branch>` under `<VAULT_PATH>`:

```bash
cat <VAULT_PATH>/<branch>/brief.md
```

The `brief.md` is a pre-computed compact extract (~8K tokens) that
captures: the latest STATUS block verbatim, open action items, recent
decisions, topic headings, and most-recent notes. It is roughly 30-60x
smaller than the source files it summarizes.

### Only fall back to source files when

- The user asks for a verbatim quote or specific historical context
- The brief explicitly says "Not in this context" for something the
  user needs
- You're debugging the brief itself or its source

Falling back means reading from `<VAULT_PATH>/<branch>/context/*.md`.

### Refreshing the brief

A cron job at `~/.local/bin/refresh-briefs.sh` refreshes briefs nightly
at 4am. If the user explicitly asks to refresh, or if the brief looks
stale relative to the source files:

```bash
cd <VAULT_PATH>
<WS_PATH> brief <branch> --budget 8000 --write
```

### Search across the vault (not in the brief)

When the user asks something the brief doesn't cover, query the index
directly instead of reading every source file:

```bash
cd <VAULT_PATH>
<WS_PATH> query "your search terms" --branch <branch> --budget 4000
```

Output is ranked + budget-capped. Use `--json` for structured parsing.

### Why this exists

Loading the source files for a typical branch in this vault costs
~57,000 input tokens. Loading the brief costs ~1,700. Same answers on
questions the brief contains, with the safety caveat that small models
occasionally hallucinate on questions outside the brief (Haiku 33%,
gpt-5.4-mini 33%, MAI 67%; frontier models 0%). Verified via a
432-trial behavioral benchmark documented in the workingset repo.

For the full safety notes and the deterministic install workflow, see
[the AGENTS.md guide](https://github.com/idanshimon/workingset/blob/main/AGENTS.md).

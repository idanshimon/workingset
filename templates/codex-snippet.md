<!--
Append this section to the project's AGENTS.md (Codex CLI convention).
Placeholders to substitute: <VAULT_PATH>, <BRANCH>, <WS_PATH>
-->

## Markdown vault context (workingset)

This project has a markdown vault at `<VAULT_PATH>` managed by
[workingset](https://github.com/idanshimon/workingset). Use the
pre-computed briefs instead of reading source files directly.

### Loading a branch

```bash
cat <VAULT_PATH>/<branch>/brief.md
```

This costs ~1.7K input tokens per load instead of ~57K for the source
files. Same content fidelity for questions the brief contains.

### Searching the vault

```bash
<WS_PATH> query "your terms" --branch <branch> --budget 4000 --json
```

JSON output is structured for downstream parsing.

### Refreshing briefs

```bash
<WS_PATH> brief <branch> --budget 8000 --write
```

A nightly cron at `~/.local/bin/refresh-briefs.sh` handles this
automatically. Manual refresh is only needed when you have just modified
source files and want the brief to reflect changes immediately.

### Fall-back to source files

Only when:
- Brief returns "Not in this context"
- User asks for verbatim historical content
- Debugging the brief itself

Falling back: `cat <VAULT_PATH>/<branch>/context/*.md`

### Safety

The brief is verified safe for frontier-tier models (Opus, Sonnet,
GPT-5, GPT-5.5) — 0% hallucination on questions outside the brief
across 432 benchmark trials. Smaller models occasionally invent
answers; if this agent uses Haiku, gpt-5.4-mini, or similar, treat
brief outputs as advisory rather than authoritative for safety-critical
work.

Full install + safety guide: [AGENTS.md](https://github.com/idanshimon/workingset/blob/main/AGENTS.md)

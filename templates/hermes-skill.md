---
name: workingset
description: "Vault-aware context compactor for the markdown vault at <VAULT_PATH>. Use when refreshing a brief, querying with BM25/FTS5, measuring before/after token cost for a load operation, or applying the same primitives to a NEW vault. CLI is `ws`. Trigger phrases: 'refresh <BRANCH> brief', 'workingset', 'ws brief', 'measure load cost', 'regenerate briefs', or any context-compaction question."
---

# workingset — vault-aware context compactor

Reusable for the vault at `<VAULT_PATH>`. Repo: <https://github.com/idanshimon/workingset>.

## Quick reference

```bash
WS=<WS_PATH>
VAULT=<VAULT_PATH>

# Generate a brief for a branch (write to <branch>/brief.md)
cd "$VAULT" && "$WS" brief <BRANCH> --budget 8000 --write

# Measure savings on a branch
cd "$VAULT" && "$WS" diff <BRANCH>

# BM25 search with a token budget
cd "$VAULT" && "$WS" query "renewal q3 budget" --branch <BRANCH> --budget 4000

# Incremental reindex (cheap, ~100ms per 1K notes)
cd "$VAULT" && "$WS" reindex
```

Every command supports `--json` for scripting from another tool.

## Loading a branch — the canonical pattern

**ALWAYS** read `<branch>/brief.md` first. Fall back to source files only when the user explicitly asks for verbatim content, or when the brief returns "not in this context" for something the user needs.

Wrong:
```bash
cat <VAULT_PATH>/<BRANCH>/context/*.md   # naive: ~57K tokens
```

Right:
```bash
cat <VAULT_PATH>/<BRANCH>/brief.md       # workingset: ~1.7K tokens (34x reduction)
```

## Budget guidance

| Use case | --budget | Why |
|---|---|---|
| Just orient me ("what's going on with X?") | 4000 | Top STATUS block + 8 actions + a few decisions |
| **Default — work from the brief without re-reads** | **8000** | Verbatim STATUS + 25 actions + 12 decisions + 50 headings + 8 recent titles |
| Big multi-stream branch | 12000 | Same shape, more breathing room |

A smaller brief saves more tokens but forces re-reads, which negate the savings. 8K is the floor for a brief an agent can actually *work* from.

**Per-model safety:** the trap-question failure mode (model invents an answer to a question whose answer isn't in the brief) varies by model. From the 432-trial behavioral benchmark:

| Model | P(hallucinate) on trap question |
|---|---|
| claude-opus-4.6 / 4.7 / 4.8 | 0% |
| claude-sonnet-4.6 | 0% |
| gpt-5.4 / gpt-5.5 | 0% |
| claude-haiku-4.5 | 33% |
| gpt-5.4-mini | 33% |
| mai-code-1-flash | 67% |

If this agent routes through a frontier model (Opus, Sonnet, GPT-5+), workingset is safe to use unconditionally. If it routes through Haiku, GPT-5.4-mini, or MAI, expect occasional confabulation on questions outside the brief. Use a `frontier model + workingset` combination for safety-critical work.

## Brief contents (v2 format)

Every brief that workingset writes has this shape, in this order:

1. **YAML frontmatter** — `vault`, `branch`, `generated_at`, `source_notes`, `kind: workingset-brief`, `version: 2`, `status_source`
2. **`## Latest status (verbatim)`** — most recent `## 🔥 STATUS (date)` block from any note in the branch, pulled in whole
3. **`## Open action items (top N)`** — every `- [ ]` checkbox across the branch, owner-tagged
4. **`## Recent decisions / owners / blockers`** — lines matching `Decision:` / `Owner:` / `Due:` / `Blocker:` / `Action:`
5. **`## Topics covered`** — H2 headings round-robined across the 10 most-recent notes
6. **`## Most recent notes`** — 8 most-recently-modified note titles + paths

Every line carries `_[source-path]_` provenance.

## When to refresh the brief

- **Automatically** if a cron job is wired (see `~/.local/bin/refresh-briefs.sh` or check via `crontab -l`)
- **On demand** when the user says "refresh <BRANCH> brief" or after any significant write to the source files
- **Stale check**: if `brief.md` mtime is > 24h old AND the source files have newer mtimes, refresh before relying on the brief

```bash
# Quick stale check
test "$(stat -f %m <VAULT_PATH>/<BRANCH>/brief.md 2>/dev/null)" -lt "$(stat -f %m <VAULT_PATH>/<BRANCH>/context/index.md 2>/dev/null)" && echo "stale"
```

## Pitfalls

- Don't ingest `brief.md` AND the underlying source files in the same load — it defeats the savings.
- Don't run `ws compact` unless the file has stacked STATUS blocks (the dry-run will tell you).
- Don't push briefs to a public repo without checking access scope — the brief contains highest-signal content from source files; if source is private, brief is private.

## Self-update

```bash
pip install --upgrade git+https://github.com/idanshimon/workingset
"$WS" --version    # confirm
```

Check [CHANGELOG.md](https://github.com/idanshimon/workingset/blob/main/CHANGELOG.md) for breaking changes between versions before upgrading.

## See also

- Full docs: <https://github.com/idanshimon/workingset/tree/main/docs>
- Agent-facing install guide: <https://github.com/idanshimon/workingset/blob/main/AGENTS.md>
- Benchmark article: <https://github.com/idanshimon/workingset> (linked from README)

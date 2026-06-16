# CLI reference

Every `ws` command, every flag, with examples. Most commands accept
`--vault PATH` (default: current directory) and `--json` (machine-readable
output). Other flags listed per-command below.

## Global

```
ws --version           Print the installed version and exit
ws --help              Show the command list
ws <command> --help    Show flags for a specific command
```

## `ws init`

Build the FTS5 index for the vault. Idempotent: safe to re-run.

```
ws init [PATH]
```

| Flag | Default | Description |
|---|---|---|
| `--vault PATH` | `.` | Vault root |

Creates `<vault>/.workingset/index.db`. Add `.workingset/` to your
`.gitignore` — the index is a build artifact.

```bash
$ ws init
Vault:      myvault  (/Users/me/myvault)
Indexed:    347 notes across 12 branches
Tokens:     412,500
Index size: 2.1 MB → /Users/me/myvault/.workingset/index.db
```

## `ws reindex`

Incremental refresh: re-index only files whose mtime has changed since
the last build. Fast.

```
ws reindex [PATH]
```

| Flag | Default | Description |
|---|---|---|
| `--vault PATH` | `.` | Vault root |
| `--full` | off | Drop the index and rebuild from scratch |

Use `--full` when you've changed indexing config or suspect corruption.

## `ws stats`

Print vault statistics — note count, branch count, total estimated tokens,
index size, last-indexed timestamp.

```
ws stats [--json]
```

`--json` output schema:

```json
{
  "vault": "myvault",
  "notes": 347,
  "branches": 12,
  "tokens_est": 412500,
  "index_size_bytes": 2150400,
  "last_indexed": "2026-06-16T08:00:34Z"
}
```

## `ws query`

BM25 search over the vault. Returns ranked branches/notes under a token
budget, ready to paste into a prompt.

```
ws query "search terms" [flags]
```

| Flag | Default | Description |
|---|---|---|
| `--branch, -b BRANCH` | none | Restrict to one branch (e.g. `cust/acme`) |
| `--budget, -B N` | 8000 | Max tokens to include in result |
| `--top, -k N` | 10 | Max number of notes to consider |
| `--boost BRANCH` | none | Apply 1.5× score boost to notes in this branch (repeatable) |
| `--full` | off | Return full note content (default: smart-trimmed snippets) |
| `--json` | off | Machine-readable JSON output |

```bash
$ ws query "renewal Q3 budget" --branch cust/acme --budget 4000
# 3 matching notes (2,156 tokens):
# 1. cust/acme/context/index.md      (score: 8.4)
# 2. cust/acme/context/opp-history.md (score: 5.2)
# 3. cust/acme/context/open-items.md  (score: 3.1)
# ...
```

JSON shape:

```json
{
  "query": "renewal Q3 budget",
  "branch": "cust/acme",
  "budget": 4000,
  "tokens_used": 2156,
  "results": [
    {"path": "...", "score": 8.4, "snippet": "..."},
    ...
  ]
}
```

## `ws brief`

Generate the L0 residual brief for a branch.

```
ws brief BRANCH [flags]
```

| Flag | Default | Description |
|---|---|---|
| `--budget, -B N` | 8000 | Total token budget for the brief |
| `--write, -w` | off | Write to `<branch>/brief.md` (otherwise print to stdout) |
| `--out PATH` | none | Write to a custom path |
| `--llm` | off | Use the optional LLM summarizer for the topic-coverage section (requires `[llm]` install group) |
| `--model MODEL` | `claude-haiku-4` | Which model to use if `--llm` is set |

If no `--write` or `--out` is passed, the brief is printed to stdout
(useful for piping or inspection).

```bash
$ ws brief cust/acme --budget 8000 --write
Wrote brief (7,842 tok, 12 notes) → /Users/me/myvault/cust/acme/brief.md

$ ws brief cust/acme --budget 4000 | head -30
---
vault: myvault
branch: cust/acme
generated_at: '2026-06-16T08:00:34Z'
source_notes: 12
kind: workingset-brief
version: 2
status_source: cust/acme/context/index.md
---
...
```

### Brief structure

Every brief has the same deterministic shape — see
[`docs/architecture.md`](architecture.md#l0--brief-pre-computed-residual)
for the full spec.

## `ws compact`

Compact stale `## 🔥 STATUS` blocks into a sidecar archive file. Lets
source notes stop growing unboundedly while preserving history.

```
ws compact FILE [flags]
```

| Flag | Default | Description |
|---|---|---|
| `--threshold N` | 3000 | Only compact if file is over N tokens |
| `--keep N` | 1 | Keep this many most-recent STATUS blocks in the source |
| `--dry-run` | off | Show what would happen without modifying files |

```bash
$ ws compact cust/acme/context/index.md --threshold 3000 --keep 1
Threshold 3000 tokens; keeping 1 most-recent STATUS block.
Archived 4 older blocks (8.2 KB) to cust/acme/context/index.archive.md.
```

If the file is under the threshold or has fewer STATUS blocks than `--keep`,
the command exits with `Skipped: only N status block(s)`.

## `ws diff`

Measure the cost of loading a branch — naive (cat all files) vs after
(read brief.md). Returns the savings ratio.

```
ws diff BRANCH [flags]
```

| Flag | Default | Description |
|---|---|---|
| `--include GLOB` | branch files | Files to include in the "before" count (repeatable) |
| `--tokenizer NAME` | `chars/4` | Token estimator: `chars/4`, `tiktoken`, `anthropic` |
| `--regenerate` | off | Regenerate brief.md in-memory before comparing (don't trust on-disk file) |

```bash
$ ws diff cust/acme
Branch: cust/acme
Tokenizer: chars/4 estimator
Files counted: 5 (210 KB)

Before (load all files):    57,141 tokens
After  (brief.md):           1,681 tokens
Ratio: 34.0x reduction (97% saved)
```

### Verifying with a real tokenizer

The default `chars/4` is fast but approximate. For provider-accurate
numbers:

```bash
$ ws diff cust/acme --tokenizer tiktoken
# uses tiktoken's o200k_base (GPT-4o / o1)

$ ws diff cust/acme --tokenizer anthropic
# uses anthropic.count_tokens (requires ANTHROPIC_API_KEY)
```

## `ws migrate`

Inspect or upgrade index + brief formats to current versions. Default is
a read-only dry-run; pass `--apply` to actually run the upgrades.

```
ws migrate [flags]
```

| Flag | Default | Description |
|---|---|---|
| `--vault PATH` | `.` | Vault root |
| `--apply` | off | Actually run migrations (default: dry-run / report only) |
| `--json` | off | Machine-readable output |

What it checks:

1. **Index schema version** — stored in `.workingset/index.db`'s
   `schema_version` table. If below `CURRENT_INDEX_VERSION` (currently 1),
   the index needs `ws reindex --full`.
2. **Brief format version** — every `brief.md` declares `version: N` in
   its frontmatter. If below `CURRENT_BRIEF_VERSION` (currently 2), the
   brief needs regenerating with `ws brief <branch> --write`.

Human-readable output:

```bash
$ ws migrate
Vault:           myvault
workingset:      v0.4.0
Index schema:    v1 (current: v1)
Briefs:          12 total, 11 current, 1 outdated, 0 unreadable

Actions needed:
  [WARNING] briefs_outdated: 1 brief(s) below current v2
    - cust/legacy/brief.md  (v1)

This was a dry run. Re-run with --apply to execute the fixes.
```

`--apply` mode:

```bash
$ ws migrate --apply
# ... same report as above, then ...
Applied:
  ✓ regenerated_brief: cust/legacy/brief.md
```

`--json` payload (envelope shape per [Working with `--json`](#working-with---json)):

```json
{
  "schema_version": "1.0",
  "command": "migrate",
  "data": {
    "vault": "myvault",
    "tool_version": "0.4.0",
    "index_version": 1,
    "current_index_version": 1,
    "current_brief_version": 2,
    "briefs": {"total": 12, "current": 11, "outdated": 1, "unreadable": 0},
    "actions_needed": [
      {"type": "briefs_outdated", "severity": "warning",
       "outdated": [{"path": "cust/legacy/brief.md", "branch": "cust/legacy",
                     "version": 1, "fix_command": "ws brief cust/legacy --budget 8000 --write"}]}
    ],
    "applied": [],
    "dry_run": true
  }
}
```

Run this after upgrading workingset to a new minor version. The
[CHANGELOG](../CHANGELOG.md) will note when version constants bump.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | User error (missing argument, invalid branch, file not found) |
| 2 | Internal error (index corruption, file write failure) |
| 130 | Interrupted (Ctrl-C) |

## Working with `--json`

Every command supports `--json` for scripting. Outputs are wrapped in a
**schema-version envelope** so downstream tools can detect breaking
changes:

```json
{
  "schema_version": "1.0",
  "command": "<command-name>",
  "data": { ... per-command payload ... }
}
```

**Versioning contract:**
- **Patch versions** (`1.0` → `1.0.1`) are stable
- **Minor bumps** (`1.0` → `1.1`) may add fields (backward-compat)
- **Major bumps** (`1.0` → `2.0`) may remove or rename fields (breaking)

Consumers should read `schema_version` first and adapt parsing. Always
read the `data` field, never assume top-level fields beyond the
envelope.

Per-command current versions:

| Command | schema_version | Payload notes |
|---|---|---|
| `init` | `1.0` | `{vault, root, indexed, branches, tokens, db_kb}` |
| `reindex` | `1.0` | `{added, updated, removed, total, tokens, db_kb}` |
| `stats` | `1.0` | `{vault, root, notes, branches, tokens, db_kb}` |
| `query` | `1.0` | `{query, branch, budget, tokens_used, results: [...]}` |
| `brief` | `1.0` | `{branch, path, tokens, notes, frontmatter}` |
| `compact` | `1.0` | `{file, before_tokens, after_tokens, archived_blocks}` |
| `diff` | `1.0` | `{branch, tokenizer, before, after, ratio, percent_saved}` |
| `migrate` | `1.0` | `{vault, index_version, current_index_version, briefs, actions_needed, applied}` |

Typical pipeline:

```bash
# Pre-compute briefs nightly
for branch in $(ws stats --json | jq -r '.data.branches_list[]'); do
  ws brief "$branch" --budget 8000 --write
done

# At query time
ws query "$user_query" --branch "$active_branch" --json | \
  jq -r '.data.results[].snippet' | \
  agent-prompt --context-from-stdin

# Detect version drift in CI
ws migrate --json | jq -e '.data.actions_needed == []' || exit 1
```

## Library API

Every CLI command has a Python equivalent. The CLI is a thin wrapper
around the library. See [`docs/architecture.md`](architecture.md) for the
class surfaces and a typed library walkthrough.

## See also

- [`docs/architecture.md`](architecture.md) — the 5-layer model + library API
- [`docs/adoption-guide.md`](adoption-guide.md) — applying workingset to a new vault
- [`CHANGELOG.md`](../CHANGELOG.md) — version history

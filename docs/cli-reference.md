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

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | User error (missing argument, invalid branch, file not found) |
| 2 | Internal error (index corruption, file write failure) |
| 130 | Interrupted (Ctrl-C) |

## Working with `--json`

Every command supports `--json` for scripting. The schemas are stable
across patch versions. Typical pipeline:

```bash
# Pre-compute briefs nightly
for branch in $(ws stats --json | jq -r '.branches_list[]'); do
  ws brief "$branch" --budget 8000 --write
done

# At query time
ws query "$user_query" --branch "$active_branch" --json | \
  jq -r '.results[].snippet' | \
  agent-prompt --context-from-stdin
```

## Library API

Every CLI command has a Python equivalent. The CLI is a thin wrapper
around the library. See [`docs/architecture.md`](architecture.md) for the
class surfaces and a typed library walkthrough.

## See also

- [`docs/architecture.md`](architecture.md) — the 5-layer model + library API
- [`docs/adoption-guide.md`](adoption-guide.md) — applying workingset to a new vault
- [`CHANGELOG.md`](../CHANGELOG.md) — version history

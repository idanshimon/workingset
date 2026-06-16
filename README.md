# workingset

> Vault-aware context compactor for LLM agents. Treat your markdown vault like a working set, not a buffer.

Point `workingset` at any folder of markdown — Obsidian vault, customer-hub repo, agent-os tree, anything — and get four primitives that solve the real-world version of the "context window blew up" problem:

1. **Index** — SQLite FTS5 over the whole vault, sub-millisecond keyword routing, ~1 second to build a full index on a 1300-note vault.
2. **Working set** — `ws query "kapil 30x"` returns ranked branches under a token budget, ready to paste into a prompt.
3. **Brief** — `ws brief cust/hca` writes a ~500-token L0 residual that any agent can load instead of reading 5 files / 220KB.
4. **Compact** — `ws compact path/to/index.md` moves stale "🔥 STATUS" blocks out to a sibling archive when the file exceeds 3000 tokens (the [ContextForge](https://github.com/Betanu701/ContextForge) §4.2 rule), leaving a pointer behind.

No vector DB. No embeddings server. No LLM call required for the index path. Pure Python + SQLite. The LLM is *optional*, used only when you ask for it (`--llm anthropic` / `--llm openai`).

## Why

Modern coding agents (Hermes, Claude Code, Copilot CLI, Cursor) all hit the same wall: when your knowledge base is structured markdown — customer notes, project docs, daily logs — and you say "load the customer," the agent reads every file in the folder and burns 50K+ tokens before you've asked anything. The agent doesn't have access to model-side KV cache. You can't make Anthropic or GitHub do the smart thing for you.

But you *can* do the smart thing on the markdown layer. That's what this is.

The architecture is a direct application of [Derek Thomas's ContextForge paper](https://github.com/Betanu701/ContextForge) and the [Codebase-Memory paper (arXiv 2603.27277)](https://arxiv.org/abs/2603.27277), translated from "your model's KV cache" to "your filesystem of notes."

## Install

```bash
pip install workingset
# or, with optional LLM summarizers:
pip install "workingset[llm]"
```

Requires Python 3.11+.

## Quickstart

```bash
cd ~/path/to/your/vault
ws init                         # one-time index build
ws stats                        # confirm: notes / branches / tokens / db size
ws query "kapil 30x token"      # BM25 search, prints top hits + snippets
ws query "kapil" --branch cust/hca --budget 4000     # scoped working set
ws brief cust/hca --write       # writes cust/hca/brief.md (~500 tok)
ws compact cust/hca/index.md    # archive stale STATUS blocks
```

Every command takes `--json` for scripting from any agent harness.

## Token estimation accuracy

Token counts here are **approximate**. `workingset` uses a `chars/4` heuristic that tracks ContextForge's own approach — fast, dependency-free, and good enough for budget arithmetic and ratio reporting. It is NOT what your provider bills you for.

For provider-accurate counts (Anthropic, OpenAI, etc.), pipe the output of `ws brief --json` through the relevant tokenizer (`tiktoken`, `anthropic.count_tokens`, etc.). The estimator here is calibrated for English markdown; expect ±20% drift on heavily code-laden or non-English content.

The published `ws diff` ratio is stable under this approximation — both "before" and "after" use the same estimator, so the relative reduction holds even when absolute numbers are off.

## Verified on a real vault

On `customer-hub` (the workflow that motivated this project — 1,233 notes, 2.04M tokens, 52 branches):

```
ws diff cust/hca --budget 500
  Files counted: 5 (214 KB)
  Before (load all files):  53,942 tokens
  After  (brief @  500):       472 tokens
  Ratio: 114.3x reduction (99.1% saved)

ws diff cust/hca --budget 1500
  Before (load all files):  53,942 tokens
  After  (brief @ 1500):     1,275 tokens
  Ratio: 42.3x reduction (97.6% saved)
  Brief contents: 8 actions · 5 decisions · 15 headings
```

The 1500-budget brief is the one you'd actually wire into a skill — full enough to act on, still 42× cheaper than the file dump it replaces.

## Architecture

`workingset` implements 3 of the 5 layers from ContextForge — the layers that don't require model-side access:

| Layer | What it is here | Where it lives |
|---|---|---|
| **L0 Residual** | The brief — pre-computed ~500-token summary of a branch | `<branch>/brief.md` |
| **L1 Branch cache** | The working set — query result, packed under token budget | in-memory per call |
| **L2 Memory index** | SQLite FTS5 inverted index, BM25 ranked | `<vault>/.workingset/index.db` |
| **L3 Knowledge store** | The vault itself | your filesystem |
| L−1 LoRA | (skipped — requires self-hosted model weights, doesn't apply) | — |

The "branch" concept maps to a top-level folder under your vault root, with one carrier-aware exception: `cust/<slug>`, `customers/<slug>`, `accounts/<slug>`, `projects/<slug>`, `clients/<slug>` collapse to a 2-level branch. Customize via `Vault(..., extra_carriers=...)` if you need different shapes.

## Library use

```python
from pathlib import Path
from workingset import Vault, VaultIndex
from workingset.brief import BriefGenerator
from workingset.compact import StatusCompactor

v = Vault(Path.home() / "projects/msft/customer-hub")

with VaultIndex(v) as ix:
    ix.reindex()                                # incremental
    results, used = ix.working_set(
        "kapil 30x token reduction",
        budget_tokens=4000,
        boost_branches=["cust/hca"],            # 1.5× boost for active customer
    )
    for r in results:
        print(f"{r.title}  [{r.relpath}]  ({r.token_estimate} tok)")

# Generate a brief and write it next to the branch.
gen = BriefGenerator(v, ix, budget_tokens=500)
brief = gen.for_branch("cust/hca")
brief.write(v.root / "cust/hca/brief.md")

# Compact stale status blocks.
StatusCompactor(threshold_tokens=3000).compact(
    v.root / "cust/hca/context/index.md"
)
```

## CLI reference

```
ws init              one-time full index build
ws reindex           incremental refresh (use --full to rebuild)
ws stats             show index stats
ws query <query>     BM25 search; --branch, --budget, --boost, --top, --full
ws brief [branch]    generate L0 residual; --out, --write, --llm, --budget
ws compact <file>    compact stale status blocks; --threshold, --keep, --dry-run, --llm
```

All commands accept `--vault PATH` (defaults to current directory) and `--json` for machine-readable output.

## What this is not

- **Not a vector DB.** BM25/FTS5 only. Add embeddings on top if you want — there's a clean `SearchResult.score` to weight against.
- **Not an Obsidian plugin.** It reads the filesystem directly. Works alongside Obsidian, doesn't require it.
- **Not a model harness.** It produces context, doesn't call models. Plug the output into Hermes / Claude Code / Copilot CLI / your own agent.
- **Not a replacement for ContextForge.** ContextForge operates on the model's KV cache and unlocks "infinite context" for agents that own the model deployment. `workingset` operates on the markdown layer and unlocks "5K-token loads" for agents that don't. They compose.

## Credit

The 5-layer architecture, the 3000-token compaction trigger, the verbatim summarization prompt, and the "compact-to-signatures" idea all come from Derek Thomas's [ContextForge](https://github.com/Betanu701/ContextForge) (2026). The decision to apply it at the markdown-vault layer instead of the KV-cache layer is what `workingset` adds.

## License

MIT.

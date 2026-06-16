# workingset

> Vault-aware context compactor for LLM agents. Treat your markdown vault like a working set, not a buffer.

Point `workingset` at any folder of markdown — Obsidian vault, customer-notes repo, agent-os tree, anything — and get four primitives that solve the real-world version of the "context window blew up" problem:

1. **Index** — SQLite FTS5 over the whole vault, sub-millisecond keyword routing, ~1 second to build a full index on a 1300-note vault.
2. **Working set** — `ws query "renewal Q3 budget"` returns ranked branches under a token budget, ready to paste into a prompt.
3. **Brief** — `ws brief cust/acme` writes a ~500-token L0 residual that any agent can load instead of reading 5 files / 220KB.
4. **Compact** — `ws compact path/to/index.md` moves stale "🔥 STATUS" blocks out to a sibling archive when the file exceeds 3000 tokens (the [ContextForge](https://github.com/Betanu701/ContextForge) §4.2 rule), leaving a pointer behind.

No vector DB. No embeddings server. No LLM call required for the index path. Pure Python + SQLite. The LLM is *optional*, used only when you ask for it (`--llm anthropic` / `--llm openai`).

## Why

Modern coding agents ([Hermes](https://hermes-agent.nousresearch.com), Claude Code, Copilot CLI, Cursor) and autonomous personal agents (Microsoft Scout, Agent 365) all hit the same wall: when your knowledge base is structured markdown, customer notes, project docs, daily logs, and you say "load the project," the agent reads every file in the folder and burns 50K+ tokens before you've asked anything. The agent doesn't have access to model-side KV cache. You can't make Anthropic, OpenAI, or GitHub do the smart thing for you.

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
ws query "renewal q3 budget"    # BM25 search, prints top hits + snippets
ws query "renewal" --branch cust/acme --budget 4000    # scoped working set
ws brief cust/acme --write      # writes cust/acme/brief.md (~8000 tok default)
ws compact cust/acme/index.md   # archive stale STATUS blocks
ws diff cust/acme --include "cust/acme/context/*.md"  # measure reduction
```

The default brief budget is **8,000 tokens**, sized so an agent can walk into a meeting cold and *work* from the brief, not just orient itself. Bump to `--budget 12000` for big multi-stream branches, `--budget 4000` if all you need is "what's the state of play."

Every command takes `--json` for scripting from any agent harness.

## Token estimation accuracy

Token counts here are **approximate**. `workingset` uses a `chars/4` heuristic that tracks ContextForge's own approach — fast, dependency-free, and good enough for budget arithmetic and ratio reporting. It is NOT what your provider bills you for.

For provider-accurate counts (Anthropic, OpenAI, etc.), pipe the output of `ws brief --json` through the relevant tokenizer (`tiktoken`, `anthropic.count_tokens`, etc.). The estimator here is calibrated for English markdown; expect ±20% drift on heavily code-laden or non-English content.

The published `ws diff` ratio is stable under this approximation — both "before" and "after" use the same estimator, so the relative reduction holds even when absolute numbers are off.

## Verified on a real vault

Measured end-to-end on a real customer-notes vault (1,233 notes, 52 branches, 2.04M tokens of source markdown), using **the actual `brief.md` written to disk** and **real OpenAI `o200k_base` tokenization**, that is, what an agent will actually load and what an LLM will actually bill for. The canonical "load this branch" workflow reads 5 context files totaling 208 KB.

```
ws diff cust/acme --tokenizer tiktoken \
                  --include "cust/acme/context/index.md" \
                  --include "cust/acme/context/personas.md" \
                  --include "cust/acme/context/architecture.md" \
                  --include "cust/acme/context/opp-history.md" \
                  --include "cust/acme/context/open-items.md"

  Tokenizer: tiktoken o200k_base
  Files counted: 5 (208 KB)
  Before (load all files):     57,141 tokens
  After  (brief.md on disk):    1,681 tokens
  Ratio: 34.0x reduction (97.1% saved)
```

Same measurement with the dependency-free `chars/4` estimator gives `52,403 → 1,403 = 37.4×`, about 10% optimistic vs the real tokenizer. **Always quote the tiktoken number for external claims.**

Earlier drafts of this README quoted higher ratios (72×, 114×). Those were artifacts of the original `ws diff` regenerating the brief in-memory at a budget different from what's on disk; that bug is fixed in the current `--on-disk` default. The actual win is **34× by real tokenizer** on the canonical 5-file load, still substantial, just honest.

The 8K-default brief is sized so an agent can walk into a meeting cold, work from the brief, and not need to re-read source files just to get oriented. Pick `--budget 4000` for orient-only, `--budget 12000` for large or multi-stream branches.

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

v = Vault(Path.home() / "vault")

with VaultIndex(v) as ix:
    ix.reindex()                                # incremental
    results, used = ix.working_set(
        "renewal q3 budget",
        budget_tokens=4000,
        boost_branches=["cust/acme"],           # 1.5× boost for active branch
    )
    for r in results:
        print(f"{r.title}  [{r.relpath}]  ({r.token_estimate} tok)")

# Generate a brief and write it next to the branch.
gen = BriefGenerator(v, ix, budget_tokens=500)
brief = gen.for_branch("cust/acme")
brief.write(v.root / "cust/acme/brief.md")

# Compact stale status blocks.
StatusCompactor(threshold_tokens=3000).compact(
    v.root / "cust/acme/context/index.md"
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
- **Not a model harness.** It produces context, doesn't call models. Plug the output into [Hermes](https://hermes-agent.nousresearch.com) / Claude Code / Copilot CLI / Microsoft Scout / your own agent.
- **Not a replacement for ContextForge.** ContextForge operates on the model's KV cache and unlocks "infinite context" for agents that own the model deployment. `workingset` operates on the markdown layer and unlocks "5K-token loads" for agents that don't. They compose.

## Credit

The 5-layer architecture, the 3000-token compaction trigger, the verbatim summarization prompt, and the "compact-to-signatures" idea all come from Derek Thomas's [ContextForge](https://github.com/Betanu701/ContextForge) (2026). The decision to apply it at the markdown-vault layer instead of the KV-cache layer is what `workingset` adds.

The behavioral benchmark that validated workingset across 7 models (and surfaced a small-model failure mode worth knowing about) was run on [Hermes Agent](https://hermes-agent.nousresearch.com) by Nous Research, the open-source agent runtime workingset is most often paired with. Cross-vendor model access for that benchmark came through the [GitHub Copilot CLI](https://github.com/github/copilot-cli) catalog. The autonomous-personal-agent shape that motivates the "brief.md as L0 residual" pattern is the same shape that drives [Microsoft Scout](https://www.microsoft.com/en-us/microsoft-365/blog/2026/06/02/introducing-microsoft-scout-your-always-on-personal-agent) and Agent 365, both of which need the same "load this context cheaply" primitive even though they ship under different banners.

## License

MIT.

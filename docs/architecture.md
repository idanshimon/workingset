# Architecture

> The 5-layer mental model behind workingset, mapped from
> [ContextForge](https://github.com/Betanu701/ContextForge) (Derek Thomas).

## Why this matters

ContextForge addresses LLM context inefficiency at the model-server layer.
It needs access to the KV cache to do its job — and most third-party API
consumers (Anthropic API, GitHub Copilot CLI, OpenAI Chat Completions)
don't expose KV cache to clients.

workingset translates the same architectural ideas down to the **markdown
file layer**, which is the layer everyone can touch. Same primitives,
different layer, different access requirements.

## The 5 layers

| Layer | What it is in ContextForge | What it is in workingset | Where it lives |
|---|---|---|---|
| **L−1** | LoRA fine-tuning of the model | Skipped — needs self-hosted weights | — |
| **L0** | Residual context state | The brief: pre-computed ~8K-token compact summary | `<branch>/brief.md` |
| **L1** | Active branch cache (KV warm) | The working set: query-time result packed under a token budget | in-memory per call |
| **L2** | Memory index | SQLite FTS5 inverted index with BM25 ranking | `<vault>/.workingset/index.db` |
| **L3** | Knowledge store | The vault itself | the filesystem |

You move information up the layers (L3 → L2 → L1 → L0) by running
`ws init` / `ws reindex` (builds L2) and `ws brief` (writes L0). You move
information down at query time (L0 ⊕ L1 → fed to the agent).

## Layer detail

### L3 — Knowledge store (the vault)

The vault is just a directory of `.md` files. workingset doesn't care if
it's an Obsidian vault, a customer-notes repo, an agent-os tree, or a
hand-curated wiki. It walks the tree, parses frontmatter (YAML between
`---` markers), and infers a **branch** for each note.

The default branch convention is: top-level folder = branch. So
`cust/acme/context/index.md` belongs to branch `cust/acme`. Customize via
`Vault(extra_carriers=...)` if your layout differs.

```python
from workingset import Vault
v = Vault.from_path("/path/to/vault")
for note in v.walk():
    print(note.branch, note.path, note.frontmatter)
```

### L2 — Memory index (SQLite FTS5)

`ws init` creates `.workingset/index.db`. This is an SQLite database with
one FTS5 virtual table over note content + one regular table for metadata.

- **Indexing strategy:** content tokenized with FTS5's default tokenizer
  (`unicode61`); titles + path + branch get extra weight via BM25 column
  weighting
- **Prefix queries:** the schema declares `prefix='2 3 4'` so prefix
  searches (`renewal*`) hit the index
- **Incremental:** `ws reindex` walks the vault, compares file mtimes
  against the indexed-at column, only re-indexes changed notes
  (~100ms on a 1,300-note vault)
- **Storage:** small. ~1KB per note on average

The index is a build artifact. Always `.gitignore` the `.workingset/`
directory.

### L1 — Working set (query-time)

When an agent calls `ws query "renewal Q3"`, workingset:

1. Runs BM25 against the FTS5 index, scoring all notes
2. Optionally restricts to one branch (`--branch cust/acme`)
3. Applies a 1.5× boost to notes whose branch matches the active branch
   (the "branch cache boost" — ContextForge §4.1)
4. Packs the top-scored notes into the result, in score order, until the
   `--budget` token limit is reached
5. Returns the packed result with provenance

Output is plain text by default; pass `--json` for scripting from another
tool.

### L0 — Brief (pre-computed residual)

`ws brief <branch> --write` extracts the highest-signal content from a
branch into a compact, deterministic-shape `brief.md`. The shape, in
order:

1. **YAML frontmatter** — `vault`, `branch`, `generated_at`, `source_notes`,
   `kind: workingset-brief`, `version: 2`, `status_source`
2. **`## Latest status (verbatim)`** — most recent `## 🔥 STATUS (date)`
   block from any note in the branch, pulled in WHOLE. Highest signal
   density.
3. **`## Open action items (top N)`** — every `- [ ]` checkbox across
   the branch, owner-tagged when possible
4. **`## Recent decisions / owners / blockers`** — lines matching
   `Decision:` / `Owner:` / `Due:` / `Blocker:` / `Action:`
5. **`## Topics covered`** — H2 headings round-robined across the 10
   most-recent notes (no single sprawling note hogs the budget)
6. **`## Most recent notes`** — 8 most-recently-modified note titles +
   paths

Every line carries `_[source-path]_` provenance, so the agent can fall
back to source files when it needs verbatim content.

### Section budgets

The `--budget` flag is divided across the brief's sections via the
`SectionBudget` dataclass. Defaults for an 8K-token brief:

| Section | Share | Why |
|---|---|---|
| Status (verbatim) | unlimited† | The most-recent STATUS block is highest-signal; never trim it |
| Open action items | 35% | Owner-tagged checkboxes are the second-highest signal |
| Decisions / blockers | 20% | "What's true vs aspirational" data |
| Topics covered | 25% | Round-robin so no single note hogs the budget |
| Most recent notes | 10% | Discovery — titles + paths for further exploration |
| Frontmatter overhead | ~10% | YAML metadata block |

† STATUS gets a hard cap if it's bigger than 60% of the total budget,
trimmed to keep the header + first content paragraph.

To customize, instantiate `BriefGenerator(SectionBudget(...))` from Python
rather than from the CLI:

```python
from workingset import Vault
from workingset.brief import BriefGenerator, SectionBudget

v = Vault.from_path("/path/to/vault")
budgets = SectionBudget(
    total=12000,
    actions_share=0.40,    # more weight on action items
    decisions_share=0.25,  # more weight on decisions
    topics_share=0.20,
    recent_share=0.05,
)
bg = BriefGenerator(v, budgets)
bg.write_brief("cust/acme")
```

## Schema versioning

workingset stamps version numbers in two places so changes to formats
can be detected and migrated:

| What | Where | Bumped when |
|---|---|---|
| **Index schema version** | `.workingset/index.db`'s `schema_version` table | The SQLite FTS5 schema changes such that existing indexes can't be incrementally updated |
| **Brief format version** | Each `brief.md`'s frontmatter `version:` field | The brief's structure changes such that downstream consumers (OIL graph builders, agent skills) need to adapt parsing |
| **JSON output schema version** | Envelope `schema_version` field on every `--json` output | A `--json` payload changes shape (field renamed/removed) |

Current values (constants in `src/workingset/cli.py`):

```python
CURRENT_INDEX_VERSION = 1
CURRENT_BRIEF_VERSION = 2
SCHEMA_VERSIONS = {
    "init": "1.0", "reindex": "1.0", "stats": "1.0", "query": "1.0",
    "brief": "1.0", "compact": "1.0", "diff": "1.0", "migrate": "1.0",
}
```

### Migration workflow

Users run `ws migrate` after upgrading workingset:

```bash
ws migrate           # dry-run: report what's outdated
ws migrate --apply   # repair: rebuild index + regenerate briefs
```

The migrate command checks:
1. Does `.workingset/index.db` have a `schema_version` table, and is the
   stored value ≥ `CURRENT_INDEX_VERSION`?
2. Does every `brief.md` have `version: N` in frontmatter, and is
   `N >= CURRENT_BRIEF_VERSION`?

Anything below current is flagged with a `fix_command` (e.g.
`ws reindex --full`, `ws brief cust/acme --budget 8000 --write`).

### When to bump

| Change | What to bump |
|---|---|
| Add a new optional field to a brief section | nothing — readers should tolerate extra fields |
| Rename a brief section heading | bump `CURRENT_BRIEF_VERSION` |
| Add a new `--json` field | bump minor (`1.0` → `1.1`); old consumers still work |
| Remove or rename a `--json` field | bump major (`1.0` → `2.0`); document the migration |
| Change the FTS5 schema | bump `CURRENT_INDEX_VERSION` and add migration to `ws migrate --apply` |

### Why three separate version constants

The three changes happen on independent timescales:

- Index schema is stable for years (FTS5 BM25 is mature)
- Brief format evolves with feedback (already bumped 1→2 in v0.2)
- JSON envelopes evolve as new commands are added or output expands

Tying them together would force unnecessary migrations every time any
one of them changed.

## Reproducible benchmark

The headline workingset claims (30-60× token reduction, per-model
hallucination probabilities on the trap question) are validated by a
behavioral benchmark that ships with the repo at [`bench/`](../bench/).

It is the public, self-contained subset of the 432-trial study from the
[benchmark article](https://github.com/idanshimon/workingset). Pointed
at the [example vault](../examples/example-vault/) so anyone can re-run
without access to private data:

```bash
cd bench
python3 harness/run_minimal_bench.py    # ~3 min, ~$0.50
python3 harness/render_report.py        # produces report.html
open report.html
```

The harness measures three questions across two conditions
(`brief_only` vs `full_source`) per model, parses the Copilot CLI
billing footer for actual input tokens, and grades each response as
correct/abstained/hallucinated. The grading uses keyword sets (no LLM
judge) so it's deterministic and free.

The trap question (Q3: "How many in-house developers does Acme have?")
is the safety primitive. The brief contains "Priya's 10-20 core
engineers" in a *different* context (workshop attendance). A model that
confuses workshop-attendance with developer-headcount will report
"10-20" as the answer. Frontier models recognize this and abstain;
small models sometimes don't.

Use this harness as a methodology template, not a value claim for your
vault. Your data shape will produce different ratios and different trap
profiles. Add your own questions to the harness for production use.

## Why no embeddings

Common question: "why BM25/FTS5 instead of vector embeddings?"

Three reasons:

1. **Customer-notes queries are entity-heavy.** Names, customer slugs,
   project codenames, dates. BM25 nails entity matches; embeddings drift
   into "things vaguely related to your query."
2. **No external dependency.** SQLite is stdlib in Python. Embeddings
   require sentence-transformers (~400MB) or an API call per query.
3. **No failing test demonstrates the gap.** When a real-vault query
   surfaces something BM25 misses, captured as a failing test, made to
   pass — that's when adding embeddings is justified. Until then, no.

The hybrid retrieval path is intentionally not built. If you need it,
open an issue with the failing query.

## Data flow at runtime

```
agent process            workingset                      vault filesystem
─────────────            ──────────                      ────────────────
                                                                │
"need context for         ┌─────────────────┐                   │
 cust/acme"      ────────▶│ open brief.md   │◀──────────────────┤
                          │ (L0, ~5KB)      │                   │
                          └────────┬────────┘                   │
                                   │                            │
                                   ▼                            │
                          1,681 tokens                          │
                          fed into prompt                       │
                                                                │
                                                                │
"need detail on what      ┌─────────────────┐                   │
 happened in May"────────▶│ ws query        │                   │
                          │   ↓             │                   │
                          │ FTS5 lookup     │◀──────────────────┤
                          │   ↓             │   .workingset/    │
                          │ top-N branches  │   index.db (L2)   │
                          │   ↓             │                   │
                          │ pack to budget  │                   │
                          └────────┬────────┘                   │
                                   │                            │
                                   ▼                            │
                          ranked snippets                       │
                          fed into prompt                       │
```

The brief (L0) handles 80% of "load context" cases. The query (L1) handles
"I need to drill into something specific" cases. The vault (L3) is the
ground truth either layer can fall back to.

## Compaction (the offline maintenance pass)

`ws compact <file>` implements ContextForge §4.2's
status-archive sweeper: when a file has multiple stacked `## 🔥 STATUS
(date)` blocks accumulated over time, move all but the most recent N
blocks to a sidecar `<file>.archive.md`.

This is a separate concern from brief generation. The brief always
extracts the most-recent status block; compaction stops the source file
from growing unboundedly over time.

```bash
ws compact cust/acme/context/index.md --threshold 3000 --keep 1
# "Threshold 3000 tokens; keeping 1 most-recent STATUS block.
#  Archived 4 older blocks (8.2 KB) to cust/acme/context/index.archive.md."
```

## Token estimation

workingset's default tokenizer is a `chars/4` heuristic. This is:

- Model-agnostic (works without any model client installed)
- Roughly accurate for English markdown (±15-20%)
- **Not** what an API provider actually bills you

For provider-accurate counts, pass `--tokenizer tiktoken` (requires
`pip install tiktoken`) or `--tokenizer anthropic` (requires `anthropic`
in the optional `[llm]` install group).

`ws diff` ratios are stable under the heuristic because both sides use
the same estimator. Absolute numbers shift; ratios don't.

## See also

- [`CONTRIBUTING.md`](../CONTRIBUTING.md) — dev setup, PR conventions
- [`docs/cli-reference.md`](cli-reference.md) — every CLI command + flag
- [`docs/adoption-guide.md`](adoption-guide.md) — recipe for applying
  workingset to a new vault

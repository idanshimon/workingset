# Adoption guide

How to wire `workingset` into a vault you already have. The whole
recipe is 6 steps, runs in about 15 minutes, and lets you decide
whether to keep going *before* you automate anything.

## Step 0 — Decide if workingset fits your vault

workingset earns its keep on vaults that look like accumulated
working memory:

- **Customer notes** — recurring meeting notes, accumulated action items,
  stacked status updates
- **Project trackers** — running notes with status blocks and assignees
- **Research / OSINT vaults** — many notes per subject, status blocks
  per investigation
- **Agent OS / personal knowledge base** — fits, but the savings ratio
  is often smaller than the customer-notes case

workingset is **not** ideal for:

- **Reference documentation** — Markdown books, language tutorials, API
  reference. These are flat, structured, and don't have a "most recent
  status." Use full-text search directly.
- **Code repositories** — different shape entirely (code semantics matter
  more than markdown structure). Use the existing code-aware tools.
- **Pure prose** — essays, articles, novels. Same issue: no status blocks
  or owner-tagged actions to extract.

If you're not sure, run Step 3 first. The `ws diff` number will tell you.

## Step 1 — Install

```bash
pip install git+https://github.com/idanshimon/workingset
# (or clone + pip install -e ".[dev]" for hacking)
```

## Step 2 — Initialize against your vault

```bash
cd ~/path/to/your/vault
ws init
```

This builds `.workingset/index.db` (one SQLite file). Idempotent.

**Add to `.gitignore`:**

```bash
echo ".workingset/" >> .gitignore
```

The index is a build artifact. Always.

## Step 3 — Measure first

Before automating anything, find out if workingset actually helps on
your data:

```bash
ws diff some/branch --regenerate
```

Output looks like:

```
Branch: some/branch
Tokenizer: chars/4 estimator
Files counted: 8 (340 KB)

Before (load all files):    81,200 tokens
After (brief.md, regenerated): 7,840 tokens
Ratio: 10.4x reduction (90.4% saved)
```

**Decision point:**

- **Ratio < 2×:** workingset probably isn't right for this vault. Stop here.
- **Ratio 2-5×:** modest savings. Worth it if you load this branch often.
- **Ratio > 5×:** significant. Continue.

The verified number on the customer-notes corpus the tool was designed
for is 34×.

## Step 4 — Generate a brief for one branch

```bash
ws brief some/branch --budget 8000 --write
```

Inspect the result:

```bash
less some/branch/brief.md
```

You should see:

- YAML frontmatter with vault / branch / generated_at / source_notes
- A `## Latest status (verbatim)` section if your branch has any
  `## 🔥 STATUS (date)` blocks
- A `## Open action items` section if your branch has `- [ ]` checkboxes
- A `## Recent decisions / owners / blockers` section
- A `## Topics covered` section (H2 headings from recent notes)
- A `## Most recent notes` section (titles + paths)

**If a section is empty:**

- No STATUS block in this branch? That section is omitted. (workingset
  works with any subset; only the STATUS extractor needs specific markdown.)
- No action items? The actions section is omitted.

**If the brief feels too sparse:**

```bash
ws brief some/branch --budget 12000 --write
```

The default of 8K is the floor for "an agent can actually work from this."
Bigger budgets fit more, smaller budgets force re-reads.

## Step 5 — Wire it into your agent

The pattern: **read `brief.md` first, fall back to source files on demand.**

### If you use Python directly

```python
def load_branch(branch: str) -> str:
    """Load context for a vault branch — brief-first."""
    brief = Path(f"{branch}/brief.md")
    if brief.exists():
        return brief.read_text()
    # Fall back to concatenating source files
    return "\n\n".join(f.read_text() for f in Path(branch).glob("context/*.md"))
```

### If you use Claude Code / Codex CLI / Cursor

Add to your project rules (`CLAUDE.md`, `AGENTS.md`, `.cursor/rules`):

```markdown
## When loading context from cust/<name>/

ALWAYS read cust/<name>/brief.md first. The brief is pre-computed by
the workingset tool (`ws brief cust/<name> --budget 8000 --write`) and
contains the highest-signal extract of all notes in the branch.

Only read the underlying source files (cust/<name>/context/*.md) when:
- The user asks for a verbatim quote or specific historical context
- The brief explicitly says "not in this context" for something the user needs
- You're debugging the brief itself

Doing this saves ~30x in input tokens per load.
```

### If you use Hermes Agent

Create a skill at `~/.hermes/skills/<your-namespace>/<vault-name>/SKILL.md`:

```markdown
---
name: my-vault-context
description: "Loads my-vault context via workingset brief. Trigger: load my-vault, refresh my-vault brief."
---

# Loading my-vault context

ALWAYS read `<vault>/brief.md` first via `ws brief <branch>` or directly
from disk. Only fall back to source files when explicitly needed.

## Commands

WS=~/projects/workingset/.venv/bin/ws
$WS brief <branch> --budget 8000 --write   # refresh
$WS diff <branch>                          # measure
$WS query "..." --branch <branch>          # search
```

## Step 6 — Automate the refresh

Briefs go stale. Refresh on a schedule.

Save this as `~/.local/bin/refresh-briefs.sh`:

```bash
#!/bin/bash
set -euo pipefail
WS=~/path/to/workingset/.venv/bin/ws
VAULT=~/path/to/your/vault

cd "$VAULT"
"$WS" reindex
for branch in cust/*; do
  [ -d "$branch" ] && "$WS" brief "$branch" --budget 8000 --write
done
```

```bash
chmod +x ~/.local/bin/refresh-briefs.sh
```

Add to crontab:

```cron
0 4 * * *  /Users/me/.local/bin/refresh-briefs.sh > /tmp/workingset.log 2>&1
```

**On macOS specifically:** `cron` is deprecated; use `launchd` for
production. A simple `crontab -e` still works for personal use.

## Verification: did the wiring help?

Measure with your agent before AND after, on a real load operation.
For example:

```bash
# Before workingset (naive load)
time agent-cli prompt "summarize my-vault's cust/acme" \
  --context-files "cust/acme/context/*.md"

# After workingset (brief load)
time agent-cli prompt "summarize my-vault's cust/acme" \
  --context-file "cust/acme/brief.md"
```

Compare:

1. **Input tokens billed** — the metric workingset is designed to move
2. **Wall time** — likely similar or slightly slower (the agent harness
   overhead usually dominates; see the [benchmark article](https://github.com/idanshimon/workingset))
3. **Answer quality** — the safety-critical metric

For (3), keep a list of questions you ask this vault often. Run them
both ways. If the brief-only path gives the same answers as the
full-source path, you're done.

If the brief-only path occasionally hallucinates on questions whose
answers aren't in the brief, that's the trap-question pattern documented
in the benchmark — see the [behavioral safety section](#what-can-go-wrong-and-how-to-detect-it)
below.

## What can go wrong (and how to detect it)

**Brief misses content you expect.** Increase `--budget`. If it still
misses, the content may be in a section workingset doesn't extract
(it focuses on STATUS / actions / decisions / topics / recent). Check
the source file format.

**Brief includes wrong status block.** workingset takes the most-recent
`## 🔥 STATUS (date)` block from ANY note in the branch. If you have
multiple files with STATUS blocks, the freshest wins. Use `ws compact`
to archive old ones, OR consolidate to one canonical status file.

**Agent confidently answers questions the brief doesn't contain.** This
is the "near-miss confabulation" failure mode. The brief mentions
"X-20 widgets" in one context; the agent reports "X-20" as the answer
to a different question. Mitigation:

1. Use a frontier-tier model (Opus, Sonnet, GPT-5+). The benchmark
   shows these consistently abstain on out-of-brief questions.
2. Add an explicit "If the answer is not in this context, say 'Not in
   this context.'" instruction in your agent's system prompt.
3. For safety-critical loads, test with the brief AND with the full
   source, on the same set of trap questions. Use the
   [benchmark harness pattern](https://github.com/idanshimon/workingset)
   if you want it formal.

**Brief grows beyond budget.** The STATUS block is verbatim and can push
slightly over budget. If you see briefs at 2× the requested budget,
that's a bug; please open an issue with a reproducer.

## See also

- [`docs/architecture.md`](architecture.md) — 5-layer model + section budgets
- [`docs/cli-reference.md`](cli-reference.md) — every command + flag
- [`examples/example-vault/`](../examples/example-vault) — canonical fixture
  vault you can `ws init` against to see it work
- [`CONTRIBUTING.md`](../CONTRIBUTING.md) — extending the tool or fixing bugs

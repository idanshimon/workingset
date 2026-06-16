# AGENTS.md

> Companion to [README.md](README.md), written for AI coding agents that are installing or wiring `workingset` on behalf of a user. Read this first if you are an agent acting on a user's machine. Human readers should start with [README.md](README.md) or [docs/adoption-guide.md](docs/adoption-guide.md).

## What this project is, in one paragraph

`workingset` is a Python CLI (entry point: `ws`) that reads a folder of markdown notes, builds an FTS5 index, and produces a per-folder `brief.md` an LLM agent can load instead of cat-ing the source files. The whole tool is ~600 LOC, MIT licensed, no runtime services. The brief is typically 30-60x smaller than the source it summarizes. The agent gets the same answers on questions the brief contains, abstains on questions it doesn't (with frontier-tier models), and saves measurable input tokens at the provider billing layer. Verified end-to-end on customer-notes-shaped vaults; tested across 9 LLMs in a 432-trial benchmark. See [benchmark article](https://github.com/idanshimon/workingset) for the full empirical study.

## When to install workingset on a user's vault

Install if **all three** are true:
1. The user has a folder of `.md` files they reload into an agent context repeatedly (customer notes, project trackers, research notes, agent-os style vaults, Obsidian vaults).
2. A single "load X" operation today reads more than ~10K tokens of markdown.
3. The user wants to reduce token spend OR is hitting context-window limits.

Do **not** install if:
- The vault is a code repository (workingset is markdown-specific).
- The vault is reference documentation that doesn't accumulate (API docs, language tutorials). No status blocks or owner-tagged actions to extract.
- The user already has a working compaction pipeline they're happy with.

When in doubt, run Step 3 below first. The `ws diff` ratio tells you objectively.

## Agent install workflow (deterministic, with verification)

Each step has a command, expected output, and what to do on failure. Skip none.

### Step 1 - Verify environment

```bash
python3 --version    # must be >= 3.11
which python3        # capture path for venv creation
```

Success: `Python 3.11.x`, `3.12.x`, or `3.13.x`.
Failure: tell the user to install Python 3.11+; do not proceed.

### Step 2 - Install workingset

```bash
pip install git+https://github.com/idanshimon/workingset
ws --version    # verify
```

Success: `ws, version 0.3.0` (or higher).
Failure modes:
- `command not found: ws` -> the user's pip install location is not in PATH. Look for `~/.local/bin/ws` or the active venv's `bin/ws`. Add to PATH or invoke directly.
- `pip install` permission error -> propose a virtualenv: `python3 -m venv .venv && source .venv/bin/activate && pip install git+https://github.com/idanshimon/workingset`.

### Step 3 - Measure first

Ask the user for their vault path and a representative branch (a top-level folder under the vault). Then:

```bash
cd <user-vault-path>
ws init                       # one-time index build
ws diff <branch> --regenerate
```

Expected output looks like:

```
Branch: <branch>
Tokenizer: chars/4 estimator
Files counted: N (XYZ KB)
Before (load all files):  XX,XXX tokens
After (brief.md, regenerated): X,XXX tokens
Ratio: N.Nx reduction (XX% saved)
```

**Decision:**
- Ratio < 2.0x: do not install. Tell the user the savings don't justify the wiring; suggest they keep their current workflow.
- Ratio 2.0x - 5.0x: modest. Confirm with the user before continuing.
- Ratio > 5.0x: continue without confirming, this is workingset's target use case.

### Step 4 - Generate the first persistent brief

```bash
ws brief <branch> --budget 8000 --write
```

Success: `Wrote brief (~7-8K tok, N notes) -> <branch>/brief.md`.

The 8000-token default is the floor for "agent can actually work from the brief without re-reads." Do not silently regress to smaller budgets — the v0.2 default was raised from 500 -> 8000 after user feedback that smaller briefs forced too many re-reads.

If the brief looks too sparse, retry with `--budget 12000`. If too dense, `--budget 6000`. Inspect with the user before automating.

### Step 5 - Add `.workingset/` to `.gitignore`

The SQLite index is a build artifact, not source.

```bash
echo ".workingset/" >> <user-vault-path>/.gitignore
```

Check if it's already there before appending. If the vault is not a git repo, skip silently.

### Step 6 - Wire integration into the user's agent

This is where you (the agent) earn your keep. Pick the integration that matches the user's primary agent surface. **Use the canonical template files in [`templates/`](templates/) so you don't reinvent the wording.**

| User's agent surface | What to do | Template file |
|---|---|---|
| **Hermes Agent** (Nous Research) | Create skill at `~/.hermes/skills/<namespace>/workingset/SKILL.md` | [`templates/hermes-skill.md`](templates/hermes-skill.md) |
| **Claude Code** | Append snippet to project `CLAUDE.md` | [`templates/claude-code-snippet.md`](templates/claude-code-snippet.md) |
| **OpenAI Codex CLI** | Append snippet to project `AGENTS.md` | [`templates/codex-snippet.md`](templates/codex-snippet.md) |
| **Cursor / Windsurf** | Append rules to `.cursor/rules` or `.windsurfrules` | [`templates/cursor-rules.md`](templates/cursor-rules.md) |
| **Custom Python harness** | Edit the user's `load_context()` function | snippet in [docs/adoption-guide.md#step-5](docs/adoption-guide.md#step-5-wire-it-into-your-agent) |

For each template:
1. Read the template file
2. Substitute placeholder tokens: `<VAULT_PATH>`, `<BRANCH>`, etc. - check the top of each template for the canonical list
3. Write/append to the target file
4. Confirm with the user that the integration was wired

### Step 7 - Automate the refresh

Copy [`templates/cron-refresh.sh`](templates/cron-refresh.sh) into the user's environment (`~/.local/bin/refresh-briefs.sh`), substitute `<VAULT_PATH>` and `<WS_PATH>` placeholders, `chmod +x`, and register with cron:

```bash
# On Linux / macOS via cron
(crontab -l 2>/dev/null; echo "0 4 * * *  ~/.local/bin/refresh-briefs.sh > /tmp/workingset.log 2>&1") | crontab -
```

On macOS, suggest the user move to `launchd` for production use; cron still works for personal.

### Step 8 - Verify the wiring end-to-end

Pick a question the user can already answer from this vault (something simple, like "who is the owner of branch X?"). Ask the user's agent twice:
1. Without workingset wiring (or with the brief deleted)
2. With workingset wiring active

Compare:
- **Token cost** (look at the agent's billing footer): should drop by the `ws diff` ratio from Step 3
- **Answer quality**: must be the same

If the answers diverge, increase `--budget` and regenerate. If they're identical, congratulate the user, point them at [README.md](README.md) for the philosophy and [docs/adoption-guide.md](docs/adoption-guide.md) for the safety section on the "trap question" failure mode.

## What to tell the user when you're done

Three things, in order:

1. **What changed on their machine**: workingset installed at `<path>`, brief created at `<branch>/brief.md`, integration wired into `<agent>` via `<template>`, cron registered for 4am daily refresh.
2. **The measured savings**: cite the actual `ws diff` ratio + the input-token delta they should see in their agent's billing.
3. **The one safety caveat**: "If the agent ever confidently answers a question whose answer isn't in the brief, that's the trap-question pattern documented in the benchmark article. Use a frontier-tier model (Opus / Sonnet / GPT-5+) and the trap rate should be 0%. Smaller models hit it ~33-67% of the time on questions where the brief contains a plausible-but-wrong number."

## How NOT to install workingset

Things agents have tried that don't work or cause damage:

- **Pasting the README install line into a global Python**: works on some systems, fails on macOS Python (PEP 668). Use a venv or `--user`.
- **Generating briefs without `--write`**: the brief prints to stdout, doesn't persist. The user thinks workingset is broken because no file appears.
- **Skipping Step 3 (`ws diff`)**: you don't actually know if workingset will help on this vault. Sometimes the answer is "no." Tell the user.
- **Inventing your own skill format**: use the canonical templates. Hermes / Claude Code / Codex / Cursor each have specific format requirements; the templates here are tested.
- **Setting cron to refresh every hour**: 4am daily is the right default. More frequent refresh adds Copilot/Anthropic API cost (if you wire LLM summarization) without adding value; briefs are usually stable over a day.
- **Pushing the user's brief.md to a public repo**: brief.md contains the highest-signal content from their vault, which may include customer names, contact info, financial figures. Ensure it's in the same access-control scope as the source files. If the vault is private, the brief must stay private.

## Calling workingset programmatically (Python library)

If your harness is Python and you want to skip the CLI shell-out:

```python
from workingset import Vault, VaultIndex
from workingset.brief import BriefGenerator, SectionBudget

vault = Vault.from_path("/path/to/vault")
index = VaultIndex(vault.root / ".workingset" / "index.db")
index.reindex(vault)

# Query
results = index.query("renewal Q3", branch="cust/acme", budget=4000)

# Brief
bg = BriefGenerator(vault, SectionBudget(total=8000))
brief_text = bg.build_brief("cust/acme")
# or: bg.write_brief("cust/acme")
```

Full library API documented in [docs/architecture.md](docs/architecture.md#l0--brief-pre-computed-residual).

## Detection: is this vault already workingset-managed?

Quick check:

```bash
test -d <vault>/.workingset && echo "indexed"
find <vault> -name brief.md -type f | head -3
```

If `.workingset/` exists and there are `brief.md` files, the vault has workingset installed. Read those briefs first; only refresh if they're > 24h old (`stat -f %m brief.md` on macOS, `stat -c %Y brief.md` on Linux).

## Self-update path

If a user already has workingset installed and asks you to update it:

```bash
pip install --upgrade git+https://github.com/idanshimon/workingset
ws --version    # confirm new version
# Re-generate briefs in case the brief format changed (check CHANGELOG.md)
ws reindex
for branch in <vault>/cust/*; do
  [ -d "$branch" ] && ws brief "$branch" --budget 8000 --write
done
```

Check [CHANGELOG.md](CHANGELOG.md) for breaking changes between the user's current version and the new one. Version `0.x.y` semver applies: minor bumps may change the brief format, patch bumps are pure bugfixes.

## When to escalate to the user

You (the agent) should pause and confirm with the user, not silently proceed, in these cases:

- `ws diff` ratio is in the 2.0x - 5.0x ambiguous zone
- The vault has multiple plausible "branch" layouts; you need the user to pick the right top-level folder convention
- The user has an existing brief.md you'd be overwriting
- The integration step (Step 6) would modify the user's `CLAUDE.md` / `.cursor/rules` / similar — these are often hand-curated; ask first
- Cron registration (Step 7) on a system where cron isn't running or `launchd` is preferred

## See also

- [README.md](README.md) - human-facing pitch + philosophy
- [CHANGELOG.md](CHANGELOG.md) - version history, especially v0.2 -> v0.3 changes
- [docs/architecture.md](docs/architecture.md) - the 5-layer model and library API
- [docs/cli-reference.md](docs/cli-reference.md) - every CLI command + flag
- [docs/adoption-guide.md](docs/adoption-guide.md) - the human-readable version of this file
- [templates/](templates/) - drop-in templates referenced by Step 6
- [examples/example-vault/](examples/example-vault/) - canonical fictional fixture you can `ws init` against for local testing without using the user's data

"""``ws`` — the workingset CLI.

Six commands:

  ws init [path]              one-time index build
  ws reindex [path]           incremental refresh; --full to drop and rebuild
  ws stats [path]             show index size, note count, branches, last index
  ws query <query>            BM25 search; --branch, --budget, --json
  ws brief <branch>           generate L0 residual brief; --out, --llm, --write
  ws compact <file>           compact stale status blocks per ContextForge §4.2
  ws diff <branch>            measure token cost of "load <branch>" before vs after

All commands accept ``--vault PATH`` (default: cwd). Output is plain text
unless you pass ``--json``, which makes it scriptable from any agent harness.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Optional

import click

from . import __version__
from .brief import BriefGenerator
from .compact import StatusCompactor
from .index import VaultIndex
from .vault import Vault


VAULT_OPTION = click.option(
    "--vault", "-V",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Vault root (defaults to current directory).",
)
JSON_OPTION = click.option("--json", "json_out", is_flag=True, help="Emit JSON.")


# ─── JSON schema versioning ─────────────────────────────────────────────────
#
# Every --json output is wrapped in an envelope with a schema_version so
# downstream tools can detect breaking changes. Patch versions are stable;
# minor bumps may add fields (backward-compat); major bumps may remove or
# rename fields (breaking).
#
# Per-command versions live here. Bump them when you change the SHAPE of
# the corresponding payload, not when the underlying logic changes.
SCHEMA_VERSIONS = {
    "init":    "1.0",   # {vault, root, indexed, branches, tokens, db_kb}
    "reindex": "1.0",   # {vault, added, updated, removed, indexed}
    "stats":   "1.0",   # {vault, notes, branches, tokens_est, ...}
    "query":   "1.0",   # {query, branch, budget, tokens_used, results: [...]}
    "brief":   "1.0",   # {branch, path, tokens, notes, frontmatter}
    "compact": "1.0",   # {file, before_tokens, after_tokens, archived_blocks}
    "diff":    "1.0",   # {branch, tokenizer, before, after, ratio, percent_saved}
    "migrate": "1.0",   # {from_version, to_version, status, actions: [...]}
}


def _emit_json(command: str, payload: dict) -> None:
    """Wrap a payload with a schema-version envelope and print it.

    Envelope shape:
        {"schema_version": "1.0", "command": "init", "data": {...}}

    Downstream tools should read `schema_version` first and adapt parsing.
    The `data` field carries the per-command payload — see SCHEMA_VERSIONS
    above for the per-command shape contract.
    """
    envelope = {
        "schema_version": SCHEMA_VERSIONS.get(command, "0.0"),
        "command": command,
        "data": payload,
    }
    click.echo(json.dumps(envelope, indent=2))


def _resolve_vault(path: Optional[Path]) -> Vault:
    return Vault(path or Path.cwd())


def _stamp_index_schema_version(ix: "VaultIndex") -> None:
    """Write the current index schema version into the index's
    schema_version table. Called by `init` and `reindex --full` so
    `ws migrate` can detect outdated indexes later."""
    conn = ix._conn
    if conn is None:
        return  # closed index — nothing to stamp
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    conn.execute("DELETE FROM schema_version")
    conn.execute("INSERT INTO schema_version (version) VALUES (?)", (CURRENT_INDEX_VERSION,))
    conn.commit()


# Brief format version history — bump when the brief frontmatter `version:`
# field needs to change. Brief readers should check this and re-generate if
# their expected minimum is higher.
CURRENT_BRIEF_VERSION = 2

# Index schema version — bump when the SQLite FTS5 schema changes such that
# existing indexes must be rebuilt. Stored in the schema_version table.
CURRENT_INDEX_VERSION = 1


@click.group(help="workingset — vault-aware context compactor for LLM agents.")
@click.version_option(__version__, prog_name="ws")
def main() -> None:
    pass


# -- init -----------------------------------------------------------------

@main.command(help="One-time full index build (use 'reindex' for incremental).")
@VAULT_OPTION
@JSON_OPTION
def init(vault: Optional[Path], json_out: bool) -> None:
    v = _resolve_vault(vault)
    with VaultIndex(v) as ix:
        added, updated, removed = ix.reindex(full=True)
        stats = ix.stats()
        # Stamp schema_version table for future ws migrate checks
        _stamp_index_schema_version(ix)
    payload = {
        "vault": v.name,
        "root": str(v.root),
        "indexed": stats.note_count,
        "branches": stats.branches,
        "tokens": stats.total_tokens,
        "db_kb": stats.db_size_bytes // 1024,
    }
    if json_out:
        _emit_json("init", payload)
        return
    click.echo(f"Vault:      {payload['vault']}  ({payload['root']})")
    click.echo(f"Indexed:    {payload['indexed']} notes "
               f"across {payload['branches']} branches")
    click.echo(f"Tokens:     {payload['tokens']:,}")
    click.echo(f"Index size: {payload['db_kb']} KB → {ix.db_path}")


# -- reindex --------------------------------------------------------------

@main.command(help="Refresh the index. Incremental by default.")
@VAULT_OPTION
@click.option("--full", is_flag=True, help="Drop and rebuild from scratch.")
@JSON_OPTION
def reindex(vault: Optional[Path], full: bool, json_out: bool) -> None:
    v = _resolve_vault(vault)
    with VaultIndex(v) as ix:
        added, updated, removed = ix.reindex(full=full)
        stats = ix.stats()
        if full:
            _stamp_index_schema_version(ix)
    payload = {
        "added": added, "updated": updated, "removed": removed,
        "total": stats.note_count, "tokens": stats.total_tokens,
    }
    if json_out:
        _emit_json("reindex", payload)
        return
    click.echo(f"+{added} added · ~{updated} updated · -{removed} removed "
               f"→ {stats.note_count} notes / {stats.total_tokens:,} tok")


# -- stats ----------------------------------------------------------------

@main.command(help="Show index status.")
@VAULT_OPTION
@JSON_OPTION
def stats(vault: Optional[Path], json_out: bool) -> None:
    v = _resolve_vault(vault)
    with VaultIndex(v) as ix:
        s = ix.stats()
    payload = {
        "vault": v.name,
        "root": str(v.root),
        "notes": s.note_count,
        "tokens": s.total_tokens,
        "branches": s.branches,
        "db_kb": s.db_size_bytes // 1024,
        "last_indexed_at": s.last_indexed_at,
    }
    if json_out:
        _emit_json("stats", payload)
        return
    if s.note_count == 0:
        click.echo("Index is empty. Run `ws init`.")
        return
    click.echo(f"Vault:    {v.name}  ({v.root})")
    click.echo(f"Notes:    {s.note_count}")
    click.echo(f"Branches: {s.branches}")
    click.echo(f"Tokens:   {s.total_tokens:,}")
    click.echo(f"DB:       {s.db_size_bytes // 1024} KB")


# -- query ----------------------------------------------------------------

@main.command(help="BM25 search; assemble a working set under a token budget.")
@click.argument("query")
@VAULT_OPTION
@click.option("--branch", "-b", default=None, help="Restrict to one branch.")
@click.option("--budget", "-B", type=int, default=8000,
              help="Token budget for the working set (default 8000).")
@click.option("--top", "-k", type=int, default=10,
              help="Top-k results to consider (default 10).")
@click.option("--boost", multiple=True,
              help="Branch to boost 1.5×. Repeatable.")
@click.option("--full", is_flag=True,
              help="Print full body of each result, not just the snippet.")
@JSON_OPTION
def query(
    query: str,
    vault: Optional[Path],
    branch: Optional[str],
    budget: int,
    top: int,
    boost: tuple[str, ...],
    full: bool,
    json_out: bool,
) -> None:
    v = _resolve_vault(vault)
    with VaultIndex(v) as ix:
        results, used = ix.working_set(
            query, budget_tokens=budget, branch=branch,
            boost_branches=boost or None,
        )
        # Apply top-k after working-set selection so we honor the budget.
        results = results[:top]

        if json_out:
            payload = {
                "query": query,
                "budget": budget,
                "used_tokens": used,
                "results": [
                    {
                        "relpath": r.relpath, "branch": r.branch,
                        "title": r.title, "score": round(r.score, 3),
                        "tokens": r.token_estimate, "snippet": r.snippet,
                    }
                    for r in results
                ],
            }
            _emit_json("query", payload)
            return

        if not results:
            click.echo("(no matches)")
            return

        click.echo(f"# Working set for: {query}")
        click.echo(f"# Budget: {used:,} / {budget:,} tokens · "
                   f"{len(results)} results\n")
        for r in results:
            click.echo(f"## {r.title}")
            click.echo(f"_[{r.relpath}] · {r.token_estimate} tok · "
                       f"score {r.score:.2f}_\n")
            if full:
                note = v.get(r.relpath)
                if note:
                    click.echo(note.body.strip() + "\n")
            else:
                click.echo(r.snippet + "\n")


# -- brief ----------------------------------------------------------------

@main.command(help="Generate an L0 residual brief for a branch.")
@click.argument("branch", required=False)
@VAULT_OPTION
@click.option("--budget", "-B", type=int, default=8000,
              help="Target token budget for the brief (default 8000 — "
                   "size for an agent to actually work from, not just orient).")
@click.option("--out", "-o", type=click.Path(path_type=Path), default=None,
              help="Output path. Default: <vault>/<branch>/brief.md "
                   "(or <vault>/.workingset/brief-<vault>.md for whole-vault).")
@click.option("--write", is_flag=True, help="Write to --out path instead of stdout.")
@click.option("--llm", type=click.Choice(["anthropic", "openai"]), default=None,
              help="Optional LLM summarizer for over-budget briefs.")
@click.option("--llm-model", default=None, help="Override default model.")
@JSON_OPTION
def brief(
    branch: Optional[str],
    vault: Optional[Path],
    budget: int,
    out: Optional[Path],
    write: bool,
    llm: Optional[str],
    llm_model: Optional[str],
    json_out: bool,
) -> None:
    v = _resolve_vault(vault)
    summarize = None
    if llm:
        from .llm import get_summarizer
        summarize = get_summarizer(llm, model=llm_model)

    with VaultIndex(v) as ix:
        gen = BriefGenerator(v, ix, budget_tokens=budget, summarize=summarize)
        b = gen.for_branch(branch) if branch else gen.for_vault()

    if out is None and write:
        if branch:
            # Write inside the branch dir so anyone reading that folder finds it.
            out = v.root / branch / "brief.md"
        else:
            out = v.state_dir / f"brief-{v.name}.md"

    if write and out is not None:
        b.write(out)

    if json_out:
        payload = {
            "branch": b.branch, "tokens": b.stats.tokens,
            "notes": b.stats.notes_indexed, "headings": b.stats.headings_kept,
            "actions": b.stats.action_items_kept,
            "decisions": b.stats.decisions_kept,
            "written_to": str(out) if (write and out) else None,
        }
        _emit_json("brief", payload)
        return

    if write and out is not None:
        click.echo(f"Wrote brief ({b.stats.tokens} tok, "
                   f"{b.stats.notes_indexed} notes) → {out}")
    else:
        click.echo(b.content, nl=False)


# -- compact --------------------------------------------------------------

@main.command(help="Compact stale status blocks in a markdown file.")
@click.argument("path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--threshold", "-t", type=int, default=3000,
              help="Compact only when file exceeds N tokens (default 3000).")
@click.option("--keep", "-k", type=int, default=1,
              help="How many newest status blocks to keep inline (default 1).")
@click.option("--dry-run", is_flag=True, help="Don't write; report what would happen.")
@click.option("--llm", type=click.Choice(["anthropic", "openai"]), default=None,
              help="Summarize archived blocks via this provider (otherwise verbatim).")
@click.option("--llm-model", default=None)
@JSON_OPTION
def compact(
    path: Path,
    threshold: int,
    keep: int,
    dry_run: bool,
    llm: Optional[str],
    llm_model: Optional[str],
    json_out: bool,
) -> None:
    summarize = None
    if llm:
        from .llm import get_summarizer
        summarize = get_summarizer(llm, model=llm_model)

    cmp = StatusCompactor(
        threshold_tokens=threshold,
        keep_most_recent=keep,
        summarize=summarize,
    )
    result = cmp.compact(path, dry_run=dry_run)

    payload = {
        "path": str(result.path),
        "did_compact": result.did_compact,
        "skipped_reason": result.skipped_reason,
        "original_tokens": result.original_tokens,
        "compacted_tokens": result.compacted_tokens,
        "archived_tokens": result.archived_tokens,
        "archived_sections": result.archived_sections,
        "reduction_pct": round(result.reduction_pct, 1),
        "archive_path": str(result.archive_path) if result.archive_path else None,
        "dry_run": dry_run,
    }
    if json_out:
        _emit_json("compact", payload)
        return

    if result.skipped_reason:
        click.echo(f"Skipped: {result.skipped_reason}")
        return

    verb = "Would archive" if dry_run else "Archived"
    click.echo(f"{verb} {result.archived_sections} section(s)  "
               f"({result.original_tokens:,} → {result.compacted_tokens:,} tok, "
               f"{result.reduction_pct:.1f}% reduction)")
    if result.archive_path:
        click.echo(f"Archive → {result.archive_path}")


# -- diff -----------------------------------------------------------------

@main.command(help="Measure token cost: 'load <branch>' before vs after a brief.")
@click.argument("branch")
@VAULT_OPTION
@click.option("--budget", "-B", type=int, default=8000,
              help="Brief budget if regenerating (default 8000). "
                   "Ignored when --on-disk reads the existing brief.md.")
@click.option("--include", multiple=True,
              help="Glob(s) of files counted as the 'before' load. "
                   "Default: every .md file in the branch. Repeatable.")
@click.option("--regenerate/--on-disk", default=False,
              help="--on-disk (default): measure the brief.md file as it "
                   "exists on disk. --regenerate: build a fresh brief at "
                   "--budget and measure that instead.")
@click.option("--tokenizer", type=click.Choice(["chars4", "tiktoken"]),
              default="chars4",
              help="chars4 (default, dependency-free) or tiktoken "
                   "(real tokenizer, requires `pip install tiktoken`).")
@click.option("--encoding", default="o200k_base",
              help="tiktoken encoding (only with --tokenizer tiktoken). "
                   "o200k_base = GPT-4o/5, cl100k_base = older OpenAI/Claude-ish.")
@JSON_OPTION
def diff(
    branch: str,
    vault: Optional[Path],
    budget: int,
    include: tuple[str, ...],
    regenerate: bool,
    tokenizer: str,
    encoding: str,
    json_out: bool,
) -> None:
    """Compare the cost of dumping every file in a branch vs reading the brief.

    The "before" number is what an agent pays today when it does
    ``cat cust/acme/context/*.md`` (or the equivalent in your skill).
    By default the "after" number is the actual ``brief.md`` file on
    disk — i.e. what an agent will *actually* load. Use ``--regenerate``
    to instead build a fresh brief at ``--budget`` and measure that.

    Useful for proving the value of running ``ws brief`` on a cron, or for
    wiring brief.md into a customer-load skill.

    Use ``--tokenizer tiktoken`` for billable-accurate token counts.
    """
    import fnmatch

    from .brief import BriefGenerator
    from .index import VaultIndex
    from .tokens import estimate_tokens as _chars4_estimate

    v = _resolve_vault(vault)

    # Pick the tokenizer once.
    if tokenizer == "tiktoken":
        try:
            import tiktoken  # type: ignore
        except ImportError as e:
            raise click.ClickException(
                "tiktoken not installed. Run: pip install tiktoken"
            ) from e
        try:
            enc = tiktoken.get_encoding(encoding)
        except Exception as e:  # noqa: BLE001
            raise click.ClickException(
                f"Unknown tiktoken encoding {encoding!r}: {e}"
            )
        def count(text: str) -> int:
            return len(enc.encode(text))
    else:
        def count(text: str) -> int:
            return _chars4_estimate(text)

    # Find the files that count as "the load."
    branch_root = v.root / branch
    if not branch_root.is_dir():
        raise click.ClickException(f"Branch path not found: {branch_root}")

    files: list[Path] = []
    for note in v.walk():
        if note.branch != branch:
            continue
        # Never count brief.md as part of the "before" load — it's the
        # thing we're comparing against.
        if note.path.name == "brief.md":
            continue
        if include:
            rel = note.relpath
            if not any(fnmatch.fnmatch(rel, pat) for pat in include):
                continue
        files.append(note.path)

    if not files:
        raise click.ClickException(f"No notes found under branch {branch!r}")

    # "Before" cost: sum of all file contents.
    before_bytes = 0
    before_tokens = 0
    for f in files:
        text = f.read_text(encoding="utf-8", errors="replace")
        before_bytes += len(text.encode("utf-8"))
        before_tokens += count(text)

    # "After" cost: either the on-disk brief.md OR a fresh regeneration.
    brief_path = v.root / branch / "brief.md"
    brief_stats = None
    after_label = ""

    if regenerate:
        with VaultIndex(v) as ix:
            gen = BriefGenerator(v, ix, budget_tokens=budget)
            b = gen.for_branch(branch)
        # Count the fresh content with whichever tokenizer was selected.
        after_tokens = count(b.content)
        brief_stats = b.stats
        after_label = f"brief @ {budget} (regenerated, in-memory)"
    elif brief_path.is_file():
        text = brief_path.read_text(encoding="utf-8", errors="replace")
        after_tokens = count(text)
        after_label = f"brief.md (on disk, {brief_path.stat().st_size} bytes)"
    else:
        raise click.ClickException(
            f"No brief.md at {brief_path}. Run `ws brief {branch} --write` "
            f"first, or pass --regenerate to build one in-memory."
        )

    ratio = (before_tokens / after_tokens) if after_tokens else 0.0
    saved_pct = 100.0 * (1.0 - after_tokens / before_tokens) if before_tokens else 0.0

    payload = {
        "branch": branch,
        "files_counted": len(files),
        "tokenizer": tokenizer if tokenizer == "chars4" else f"tiktoken:{encoding}",
        "regenerated": regenerate,
        "before_tokens": before_tokens,
        "before_kb": before_bytes // 1024,
        "after_tokens": after_tokens,
        "after_label": after_label,
        "ratio": round(ratio, 1),
        "saved_pct": round(saved_pct, 1),
    }
    if brief_stats is not None:
        payload["brief_action_items"] = brief_stats.action_items_kept
        payload["brief_decisions"] = brief_stats.decisions_kept
        payload["brief_headings"] = brief_stats.headings_kept

    if json_out:
        _emit_json("diff", payload)
        return

    tok_label = "chars/4 estimator" if tokenizer == "chars4" else f"tiktoken {encoding}"
    click.echo(f"Branch: {branch}")
    click.echo(f"Tokenizer: {tok_label}")
    click.echo(f"Files counted: {len(files)} ({before_bytes // 1024} KB)\n")
    click.echo(f"Before (load all files): {before_tokens:>9,} tokens")
    click.echo(f"After ({after_label}): {after_tokens:>9,} tokens")
    click.echo(f"Ratio: {ratio:.1f}x reduction ({saved_pct:.1f}% saved)")
    if brief_stats is not None:
        click.echo(f"\nBrief contents: {brief_stats.action_items_kept} actions · "
                   f"{brief_stats.decisions_kept} decisions · "
                   f"{brief_stats.headings_kept} headings")


# -- migrate --------------------------------------------------------------

# (CURRENT_BRIEF_VERSION and CURRENT_INDEX_VERSION are declared near the
# top of this file alongside _stamp_index_schema_version.)


@main.command(help="Inspect or upgrade index + brief formats to current versions.")
@VAULT_OPTION
@click.option("--apply", "apply_changes", is_flag=True,
              help="Apply migration actions (default: dry-run / report only).")
@JSON_OPTION
def migrate(vault: Optional[Path], apply_changes: bool, json_out: bool) -> None:
    """Detect format-version mismatches and (optionally) repair them.

    Two things get checked:
      1. Index schema version — if behind, the index needs `init --full`
      2. Brief format version — if behind, each affected brief needs
         regenerating via `brief <branch> --write`

    Default is dry-run. Pass --apply to actually run the upgrades.
    """
    v = _resolve_vault(vault)
    actions = []

    # Check index schema version
    index_path = v.root / ".workingset" / "index.db"
    if not index_path.exists():
        actions.append({
            "type": "missing_index",
            "severity": "warning",
            "detail": "No .workingset/index.db found. Run `ws init` first.",
        })
        index_version = None
    else:
        import sqlite3
        try:
            conn = sqlite3.connect(index_path)
            cur = conn.cursor()
            cur.execute(
                "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
            )
            row = cur.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
            index_version = row[0] if row else 0
            if index_version < CURRENT_INDEX_VERSION:
                actions.append({
                    "type": "index_schema_outdated",
                    "severity": "error",
                    "detail": f"Index at schema v{index_version}, current is v{CURRENT_INDEX_VERSION}. Full rebuild required.",
                    "fix_command": "ws reindex --full",
                })
            conn.close()
        except sqlite3.DatabaseError as e:
            actions.append({
                "type": "index_corrupt",
                "severity": "error",
                "detail": f"Index unreadable: {e}",
                "fix_command": "ws init",
            })
            index_version = None

    # Check brief format versions across all branches
    brief_summary = {"total": 0, "current": 0, "outdated": 0, "unreadable": 0}
    outdated_briefs = []
    for brief_path in v.root.rglob("brief.md"):
        # Skip non-vault paths (e.g. .venv if someone put workingset INSIDE a vault)
        if any(p.startswith(".") for p in brief_path.relative_to(v.root).parts[:-1]):
            continue
        brief_summary["total"] += 1
        try:
            text = brief_path.read_text()
        except Exception:
            brief_summary["unreadable"] += 1
            continue
        if not text.startswith("---"):
            brief_summary["unreadable"] += 1
            continue
        # Parse frontmatter version
        m = re.search(r"^version:\s*(\d+)\s*$", text, re.MULTILINE)
        if not m:
            brief_summary["unreadable"] += 1
            continue
        ver = int(m.group(1))
        if ver < CURRENT_BRIEF_VERSION:
            brief_summary["outdated"] += 1
            rel = brief_path.relative_to(v.root)
            branch_parts = rel.parts[:-1]
            branch = "/".join(branch_parts) if branch_parts else "."
            outdated_briefs.append({
                "path": str(rel),
                "branch": branch,
                "version": ver,
                "fix_command": f"ws brief {branch} --budget 8000 --write",
            })
        else:
            brief_summary["current"] += 1

    if outdated_briefs:
        actions.append({
            "type": "briefs_outdated",
            "severity": "warning",
            "detail": f"{brief_summary['outdated']} brief(s) below current v{CURRENT_BRIEF_VERSION}",
            "outdated": outdated_briefs,
        })

    # Apply phase
    applied: list[dict] = []
    if apply_changes and actions:
        for act in actions:
            if act["type"] == "index_schema_outdated":
                with VaultIndex(v) as ix:
                    ix.reindex(full=True)
                applied.append({"action": "rebuilt_index", "ok": True})
            elif act["type"] == "briefs_outdated":
                with VaultIndex(v) as ix:
                    for b in outdated_briefs:
                        try:
                            gen = BriefGenerator(v, ix, budget_tokens=8000)
                            brief_obj = gen.for_branch(b["branch"])
                            brief_obj.write(v.root / b["path"])
                            applied.append({"action": "regenerated_brief", "path": b["path"], "ok": True})
                        except Exception as e:
                            applied.append({"action": "regenerated_brief", "path": b["path"], "ok": False, "error": str(e)})

    payload = {
        "vault": v.name,
        "tool_version": __version__,
        "index_version": index_version,
        "current_index_version": CURRENT_INDEX_VERSION,
        "current_brief_version": CURRENT_BRIEF_VERSION,
        "briefs": brief_summary,
        "actions_needed": actions,
        "applied": applied if apply_changes else [],
        "dry_run": not apply_changes,
    }

    if json_out:
        _emit_json("migrate", payload)
        return

    # Human-readable output
    click.echo(f"Vault:           {payload['vault']}")
    click.echo(f"workingset:      v{__version__}")
    click.echo(f"Index schema:    {'v' + str(index_version) if index_version is not None else 'missing'} "
               f"(current: v{CURRENT_INDEX_VERSION})")
    click.echo(f"Briefs:          {brief_summary['total']} total, "
               f"{brief_summary['current']} current, "
               f"{brief_summary['outdated']} outdated, "
               f"{brief_summary['unreadable']} unreadable")

    if not actions:
        click.echo("\n✓ Everything is at current versions. Nothing to migrate.")
        return

    click.echo(f"\n{'Actions needed:' if not apply_changes else 'Applied:'}")
    for act in actions:
        sev = act["severity"].upper()
        click.echo(f"  [{sev}] {act['type']}: {act['detail']}")
        if act.get("fix_command"):
            click.echo(f"    fix: {act['fix_command']}")
        if act["type"] == "briefs_outdated":
            for b in act["outdated"][:5]:
                click.echo(f"    - {b['path']}  (v{b['version']})")
            if len(act["outdated"]) > 5:
                click.echo(f"    ... and {len(act['outdated']) - 5} more")

    if not apply_changes:
        click.echo("\nThis was a dry run. Re-run with --apply to execute the fixes.")
    else:
        for a in applied:
            if not a.get("ok", True):
                click.echo(f"  [FAILED] {a}")


if __name__ == "__main__":
    main()
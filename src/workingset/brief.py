"""Brief generation — the L0 residual layer.

Given a vault and an optional branch filter, build a markdown brief that
any agent can load instead of reading 5 files / 220KB.

Default budget is 8,000 tokens — enough that an agent can walk into a
meeting cold, work from the brief, and not need to re-read source files
just to get oriented. For very large customers (50+ notes, multi-stream
engagements) bump to 12,000–15,000.

A brief consists of (in priority order):
- Frontmatter: vault name, branch, generated_at, source counts
- The most recent ``## 🔥 STATUS`` block, verbatim — highest-signal content
- Open action items (lines matching ``- [ ]``)
- Decision / owner / due / blocker lines
- Topic headings (round-robin from recent notes)
- Most-recent note titles

Budgets are enforced PER SECTION, not globally. Each section trims its own
content to fit its share of the token budget. The bottom of the brief
("most recent notes") is never silently dropped.

This is *deterministic* by default — no LLM call required. Pass a
``summarize`` callable to enable LLM-assisted compression on top.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional

import yaml

from .index import VaultIndex
from .tokens import estimate_tokens
from .vault import Note, Vault


_ACTION_ITEM_RE = re.compile(r"^\s*[-*]\s*\[([ xX])\]\s*(.+?)\s*$", re.MULTILINE)

# Decision/owner/blocker line. Matches lines like:
#   "Decision: foo"
#   "- **Owner:** Bob"
#   "**Due:** 2026-07-01"
# Group 1 = the whole matched line (used for re-emit).
# We deliberately don't try to parse out the body — we just pass the
# original line through after light normalization (strip leading list
# marker so we don't end up with "- - **Owner**" double-bullets).
_DECISION_RE = re.compile(
    r"^(\s*(?:[-*]\s+)?(?:\*\*)?(?:decision|decided|owner|due|blocker|action)(?:\*\*)?[:\s].+)$",
    re.IGNORECASE | re.MULTILINE,
)
_LIST_PREFIX_RE = re.compile(r"^\s*[-*]\s+")

# Status-block header: "## 🔥 STATUS (May 22, 2026 ...)" or variants.
# We anchor on the H2/H3 because that's how index.md status blocks are
# written. Group 1 = full header line, group 2 = date string for sorting.
_STATUS_HEADER_RE = re.compile(
    r"^(##{1,2}\s*(?:🔥\s*)?status[^\n]*?\(([^)]+)\)[^\n]*)$",
    re.IGNORECASE | re.MULTILINE,
)

# Files we generate ourselves and should never feed back into a regeneration.
# Without this filter, brief.md is treated as a source note on the next run,
# producing self-referential brief content with duplicated lines.
_BRIEF_ARTIFACT_NAMES: tuple[str, ...] = (
    "brief.md",
    # Compact-archive sidecars are also generated, not authored. We don't
    # filter them by default because users sometimes hand-edit; opt-in via
    # an explicit ignore if you want.
)


def _is_brief_artifact(relpath: str) -> bool:
    """Return True if this is a workingset-generated file we should skip."""
    name = Path(relpath).name
    return name in _BRIEF_ARTIFACT_NAMES


# The default token budget. Was 500 originally — too thin for any vault
# bigger than a sample. 8000 is enough that an agent can work from the
# brief (top 25 action items with context, last STATUS block verbatim,
# 50+ topic headings, recent meeting titles) without re-reading source
# files. Bump to 12-15K for large customers.
DEFAULT_BUDGET_TOKENS = 8000


@dataclass
class SectionBudget:
    """Per-section token allocation. Shares of the total budget.

    Defaults sized for 8K-token briefs in customer-hub-shaped vaults.
    Adjust if your corpus has a different shape (e.g. status blocks
    are not the highest-signal content for you).
    """

    actions: float = 0.30       # 30% — open action items
    status: float = 0.20        # 20% — most recent ## 🔥 STATUS block, verbatim
    decisions: float = 0.15     # 15% — decision / owner / due / blocker lines
    headings: float = 0.15      # 15% — topic headings (round-robin)
    recent: float = 0.15        # 15% — most-recent note titles
    overhead: float = 0.05      # 5%  — frontmatter + section headers

    def allocate(self, total: int) -> dict[str, int]:
        """Distribute ``total`` tokens across the sections."""
        return {
            "actions": max(200, int(total * self.actions)),
            "status": max(300, int(total * self.status)),
            "decisions": max(100, int(total * self.decisions)),
            "headings": max(100, int(total * self.headings)),
            "recent": max(80, int(total * self.recent)),
        }


def _trim_lines_to_budget(lines: list[str], budget: int) -> tuple[list[str], bool]:
    """Drop trailing lines until the joined string fits in ``budget`` tokens.

    Returns (kept_lines, was_trimmed).
    """
    if not lines:
        return [], False
    kept = list(lines)
    trimmed = False
    while kept and estimate_tokens("\n".join(kept)) > budget:
        kept.pop()
        trimmed = True
    return kept, trimmed


def _extract_latest_status(notes: list[Note]) -> tuple[Optional[str], Optional[str]]:
    """Find the most recent ``## 🔥 STATUS (date)`` block across notes.

    Walks notes in modified-time order (newest first), grabs the FIRST
    status block found, and returns ``(block_text, source_relpath)``.
    Block text spans from the header through to the next H2/H3 of equal
    or shallower depth (or end-of-file).

    Returns ``(None, None)`` if no status block exists in this branch.
    """
    for note in notes:  # caller passes notes_sorted (newest first)
        body = note.body
        m = _STATUS_HEADER_RE.search(body)
        if not m:
            continue
        start = m.start()
        header_level = len(m.group(0)) - len(m.group(0).lstrip("#"))
        # Walk forward to the next header of equal-or-shallower depth.
        cursor = m.end()
        end = len(body)
        for nm in re.finditer(r"^(#{1,6})\s", body[cursor:], re.MULTILINE):
            other = len(nm.group(1))
            if other <= header_level:
                end = cursor + nm.start()
                break
        block = body[start:end].rstrip()
        return block, note.relpath
    return None, None


def _trim_block_to_budget(text: str, budget: int) -> str:
    """Trim a block of text by paragraphs until it fits in ``budget`` tokens.

    Used for the status block, which we want to keep coherent — we can't
    just drop trailing lines because that breaks mid-bullet. Instead we
    drop trailing paragraphs and add a marker.

    If even the first paragraph (after the header) overflows, we hard-cut
    at character count on that paragraph rather than returning just the
    header — a header with no body is useless.
    """
    if estimate_tokens(text) <= budget:
        return text
    paragraphs = text.split("\n\n")
    # Always keep at least the header + first content paragraph (if any).
    # We trim from the end first, and only fall through to char-cutting
    # the first paragraph when there's nowhere else to give.
    while len(paragraphs) > 2 and estimate_tokens("\n\n".join(paragraphs)) > budget - 30:
        paragraphs.pop()
    joined = "\n\n".join(paragraphs)
    if estimate_tokens(joined) <= budget - 30:
        if len(paragraphs) < text.count("\n\n") + 1:
            return joined + "\n\n_(older paragraphs trimmed)_"
        return joined
    # Still over. Hard-cut the (first or only) content paragraph.
    if len(paragraphs) >= 2:
        header = paragraphs[0]
        content = "\n\n".join(paragraphs[1:])
        # Reserve room for header + marker + ~30 tok overhead.
        content_budget_chars = (budget - estimate_tokens(header) - 30) * 4
        if content_budget_chars > 200:
            content = content[:content_budget_chars].rstrip() + " …"
            return f"{header}\n\n{content}\n\n_(status block trimmed)_"
    # Pathological: budget is so small even the header doesn't fit.
    max_chars = max(200, budget * 4)
    return text[:max_chars].rstrip() + " …\n\n_(status block trimmed)_"


@dataclass
class BriefStats:
    """How big a brief turned out."""

    tokens: int
    notes_indexed: int
    headings_kept: int
    action_items_kept: int
    decisions_kept: int
    status_block_included: bool = False
    status_source: Optional[str] = None


@dataclass
class Brief:
    """A generated brief, ready to write to disk."""

    branch: str
    vault_name: str
    content: str
    stats: BriefStats
    sources: list[str] = field(default_factory=list)

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.content, encoding="utf-8")
        return path


class BriefGenerator:
    """Build a residual brief for a vault or a branch within it."""

    def __init__(
        self,
        vault: Vault,
        index: Optional[VaultIndex] = None,
        *,
        budget_tokens: int = DEFAULT_BUDGET_TOKENS,
        max_action_items: int = 25,
        max_decisions: int = 12,
        max_headings: int = 60,
        section_budget: Optional[SectionBudget] = None,
        summarize: Optional[Callable[[str], str]] = None,
    ) -> None:
        self.vault = vault
        self.index = index or VaultIndex(vault)
        self.budget = budget_tokens
        self.max_action_items = max_action_items
        self.max_decisions = max_decisions
        self.max_headings = max_headings
        self._budget = section_budget or SectionBudget()
        self.summarize = summarize

    def for_branch(self, branch: str) -> Brief:
        """Build a brief for a single branch (e.g. ``cust/hca``)."""
        notes = self._notes_for_branch(branch)
        if not notes:
            empty = _empty_brief(self.vault.name, branch)
            return empty
        return self._assemble(notes, branch=branch)

    def for_vault(self) -> Brief:
        """Build a vault-wide brief — all branches collapsed into one."""
        notes = [n for n in self.vault.walk() if not _is_brief_artifact(n.relpath)]
        return self._assemble(notes, branch="")

    def _notes_for_branch(self, branch: str) -> list[Note]:
        return [
            n for n in self.vault.walk()
            if n.branch == branch and not _is_brief_artifact(n.relpath)
        ]

    def _assemble(self, notes: list[Note], *, branch: str) -> Brief:
        # Sort: most-recently modified first.
        notes_sorted = sorted(notes, key=lambda n: -n.mtime_ns)

        # Pass 0: latest STATUS block, verbatim. Highest signal density —
        # this is what an agent reads first to know "where we are right now."
        status_block, status_source = _extract_latest_status(notes_sorted)

        # Pass 1: scoop the cheap, high-value signal — open action
        # items and decision/owner/due/blocker lines. These are what an
        # agent loading the brief will actually act on.
        actions: list[str] = []
        decisions: list[str] = []
        for note in notes_sorted:
            if len(actions) >= self.max_action_items and len(decisions) >= self.max_decisions:
                break
            for m in _ACTION_ITEM_RE.finditer(note.body):
                if m.group(1) == " " and len(actions) < self.max_action_items:
                    actions.append(f"- [ ] {m.group(2).strip()}  _[{note.relpath}]_")
            for m in _DECISION_RE.finditer(note.body):
                if len(decisions) < self.max_decisions:
                    line = m.group(1).strip()
                    # Skip if this line is also an action-item checkbox
                    # (action regex already grabbed it; double-emit is noise).
                    if "[ ]" in line[:8] or "[x]" in line[:8] or "[X]" in line[:8]:
                        continue
                    # Strip a leading list marker so we don't double-bullet.
                    line = _LIST_PREFIX_RE.sub("", line, count=1)
                    decisions.append(f"- {line}  _[{note.relpath}]_")

        # Pass 2: round-robin headings across the top N most-recent notes
        # so a single sprawling note doesn't hog the heading budget.
        heading_pool: list[list[str]] = []
        recent_notes = notes_sorted[: max(10, self.max_headings)]
        for note in recent_notes:
            note_headings: list[str] = []
            for line in note.body.splitlines():
                if line.startswith("## "):
                    note_headings.append(
                        f"- {line[3:].strip()}  _[{note.relpath}]_"
                    )
            if note_headings:
                heading_pool.append(note_headings)

        headings: list[str] = []
        if heading_pool:
            # Round-robin: take 1 per note until we hit max_headings.
            i = 0
            while len(headings) < self.max_headings and any(heading_pool):
                pool = heading_pool[i % len(heading_pool)]
                if pool:
                    headings.append(pool.pop(0))
                else:
                    # Drop empty pools so we don't spin.
                    heading_pool.pop(i % len(heading_pool))
                    if not heading_pool:
                        break
                    continue
                i += 1

        # Most-recent note titles as the trailing context.
        recent_titles = [
            f"- {n.title}  _[{n.relpath}]_"
            for n in notes_sorted[:8]
        ]

        sources = [n.relpath for n in notes_sorted]

        # Per-section budgets. Each section trims its OWN content; the
        # global truncation fallback is gone.
        budgets = self._budget.allocate(self.budget)
        if status_block is not None:
            status_block = _trim_block_to_budget(status_block, budgets["status"])
        actions, _ = _trim_lines_to_budget(actions, budgets["actions"])
        decisions, _ = _trim_lines_to_budget(decisions, budgets["decisions"])
        headings, _ = _trim_lines_to_budget(headings, budgets["headings"])
        recent_titles, _ = _trim_lines_to_budget(recent_titles, budgets["recent"])

        # Frontmatter.
        fm = {
            "vault": self.vault.name,
            "branch": branch or "(whole vault)",
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source_notes": len(notes),
            "kind": "workingset-brief",
            "version": 2,  # bumped: now includes ## Latest status section
        }
        if status_source:
            fm["status_source"] = status_source

        # Body — order matters: STATUS first (highest density), then
        # actionable content, then reference signal.
        sections: list[str] = []
        title = f"# Brief — {branch or self.vault.name}\n"
        sections.append(title)

        if status_block:
            sections.append(
                "## Latest status (verbatim)\n"
                f"_Source: [{status_source}]_\n\n{status_block}"
            )
        if actions:
            sections.append("## Open action items (top "
                            f"{len(actions)})\n" + "\n".join(actions))
        if decisions:
            sections.append("## Recent decisions / owners / blockers\n"
                            + "\n".join(decisions))
        if headings:
            sections.append("## Topics covered\n" + "\n".join(headings))
        if recent_titles:
            sections.append("## Most recent notes\n" + "\n".join(recent_titles))

        body = "\n\n".join(sections) + "\n"

        # If even after per-section trimming we're over budget (rare, but
        # possible on very small budgets), call the optional summarizer.
        # We no longer truncate-from-the-end as a fallback — that behavior
        # silently dropped the "Most recent notes" section.
        body_tokens = estimate_tokens(body)
        if body_tokens > self.budget * 1.2 and self.summarize is not None:
            try:
                body = self.summarize(body)
                body_tokens = estimate_tokens(body)
            except Exception:  # noqa: BLE001
                pass

        front = "---\n" + yaml.safe_dump(fm, sort_keys=False).strip() + "\n---\n\n"
        content = front + body
        stats = BriefStats(
            tokens=estimate_tokens(content),
            notes_indexed=len(notes),
            headings_kept=len(headings),
            action_items_kept=len(actions),
            decisions_kept=len(decisions),
            status_block_included=status_block is not None,
            status_source=status_source,
        )
        return Brief(
            branch=branch,
            vault_name=self.vault.name,
            content=content,
            stats=stats,
            sources=sources,
        )


def _empty_brief(vault_name: str, branch: str) -> Brief:
    fm = {
        "vault": vault_name,
        "branch": branch,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_notes": 0,
        "kind": "workingset-brief",
        "version": 1,
    }
    front = "---\n" + yaml.safe_dump(fm, sort_keys=False).strip() + "\n---\n\n"
    body = f"# Brief — {branch or vault_name}\n\n_(no notes yet)_\n"
    content = front + body
    return Brief(
        branch=branch, vault_name=vault_name, content=content,
        stats=BriefStats(
            tokens=estimate_tokens(content),
            notes_indexed=0, headings_kept=0,
            action_items_kept=0, decisions_kept=0,
        ),
    )

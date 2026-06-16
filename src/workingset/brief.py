"""Brief generation — the L0 residual layer.

Given a vault and an optional branch filter, build a small (~500-token)
markdown brief that any agent can load instead of reading 5 files / 220KB.

A brief consists of:
- Frontmatter: vault name, branch, generated_at, source counts
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


@dataclass
class SectionBudget:
    """Per-section token allocation. Shares of the total budget."""

    actions: float = 0.40       # 40% — most actionable content
    decisions: float = 0.20     # 20%
    headings: float = 0.25      # 25%
    recent: float = 0.10        # 10%
    overhead: float = 0.05      # 5% reserved for frontmatter + headers

    def allocate(self, total: int) -> dict[str, int]:
        """Distribute ``total`` tokens across the sections."""
        return {
            "actions": max(60, int(total * self.actions)),
            "decisions": max(40, int(total * self.decisions)),
            "headings": max(50, int(total * self.headings)),
            "recent": max(40, int(total * self.recent)),
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


@dataclass
class BriefStats:
    """How big a brief turned out."""

    tokens: int
    notes_indexed: int
    headings_kept: int
    action_items_kept: int
    decisions_kept: int


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
        budget_tokens: int = 500,
        max_action_items: int = 8,
        max_decisions: int = 5,
        max_headings: int = 30,
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
        notes = list(self.vault.walk())
        return self._assemble(notes, branch="")

    def _notes_for_branch(self, branch: str) -> list[Note]:
        return [n for n in self.vault.walk() if n.branch == branch]

    def _assemble(self, notes: list[Note], *, branch: str) -> Brief:
        # Sort: most-recently modified first.
        notes_sorted = sorted(notes, key=lambda n: -n.mtime_ns)

        # Pass 1: scoop the cheap, high-value signal first — open action
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
            for n in notes_sorted[:5]
        ]

        sources = [n.relpath for n in notes_sorted]

        # Per-section budgets. Each section trims its OWN content; the
        # global truncation fallback is gone.
        budgets = self._budget.allocate(self.budget)
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
            "version": 1,
        }

        # Body — order matters: most-actionable first.
        sections: list[str] = []
        title = f"# Brief — {branch or self.vault.name}\n"
        sections.append(title)

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

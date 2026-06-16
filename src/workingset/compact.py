"""Status-block compaction.

Implements ContextForge §4.2: when a "status" or "history" section in a
markdown file exceeds the compaction threshold (default 3000 tokens), the
older entries are extracted and moved to a sibling archive file, and the
original is left with only the most recent block plus a pointer.

This module is opinionated about *what* to compact, not *how* to summarize:
the LLM call is optional. By default we do mechanical compaction (keep most
recent block, archive the rest verbatim). If a provider is supplied, we run
each archived block through the ContextForge summarization prompt.

A "status block" is a section whose header matches one of:
- ``# 🔥 STATUS (date) ...``
- ``## STATUS ...``
- ``### Status ...``
- any header containing the word "status" (case-insensitive)

You can override patterns via ``StatusCompactor(extra_patterns=...)``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .tokens import estimate_tokens


# The verbatim ContextForge summarization prompt — this is the prompt
# Derek validated in his benchmarks. Don't rephrase without testing.
COMPACT_PROMPT = (
    "Summarize the following conversation concisely, preserving all key facts, "
    "decisions, and action items. Omit pleasantries and filler:\n\n"
)

DEFAULT_THRESHOLD_TOKENS = 3000

# Regex that matches a section header. Group 1 = the # marks, group 2 = title.
_HEADER_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)

# Default patterns for "status-shaped" headers.
_DEFAULT_STATUS_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^🔥\s*status", re.IGNORECASE),
    re.compile(r"\bstatus\s*\(", re.IGNORECASE),     # "STATUS (May 22, 2026)"
    re.compile(r"^status\b", re.IGNORECASE),
    re.compile(r"^current\s+state", re.IGNORECASE),
    re.compile(r"^recent\s+update", re.IGNORECASE),
)


@dataclass
class Section:
    """One header-delimited block within a markdown file."""

    level: int
    """Heading level (1..6)."""

    title: str
    """Header text without leading ``#``."""

    start: int
    """Byte offset of the first char of the header line."""

    end: int
    """Byte offset just past the last char of this section."""

    body: str
    """Section content, including its header line."""

    @property
    def token_estimate(self) -> int:
        return estimate_tokens(self.body)


@dataclass
class CompactionResult:
    """What happened when we compacted a file."""

    path: Path
    archive_path: Optional[Path]
    archived_sections: int
    original_tokens: int
    compacted_tokens: int
    archived_tokens: int
    skipped_reason: Optional[str] = None
    summaries: list[str] = field(default_factory=list)

    @property
    def did_compact(self) -> bool:
        return self.archived_sections > 0

    @property
    def reduction_pct(self) -> float:
        if self.original_tokens == 0:
            return 0.0
        return 100.0 * (1.0 - self.compacted_tokens / self.original_tokens)


class StatusCompactor:
    """Compact stale status blocks in a markdown file."""

    def __init__(
        self,
        *,
        threshold_tokens: int = DEFAULT_THRESHOLD_TOKENS,
        keep_most_recent: int = 1,
        extra_patterns: tuple[re.Pattern[str], ...] = (),
        summarize: Optional[Callable[[str], str]] = None,
    ) -> None:
        """
        Args:
            threshold_tokens: Compact only when the file exceeds this many tokens.
            keep_most_recent: How many status blocks to leave inline. Default 1.
            extra_patterns: Additional regex patterns to recognize as status blocks.
            summarize: Optional callable that takes a block of markdown and
                returns a summary string. If omitted, archived blocks are
                preserved verbatim.
        """
        self.threshold = threshold_tokens
        self.keep_most_recent = max(1, keep_most_recent)
        self.patterns = _DEFAULT_STATUS_PATTERNS + tuple(extra_patterns)
        self.summarize = summarize

    def compact(self, path: Path, *, dry_run: bool = False) -> CompactionResult:
        text = path.read_text(encoding="utf-8")
        original_tokens = estimate_tokens(text)

        result = CompactionResult(
            path=path,
            archive_path=None,
            archived_sections=0,
            original_tokens=original_tokens,
            compacted_tokens=original_tokens,
            archived_tokens=0,
        )

        if original_tokens < self.threshold:
            result.skipped_reason = (
                f"under threshold ({original_tokens} < {self.threshold} tok)"
            )
            return result

        sections = parse_sections(text)
        status_sections = [s for s in sections if self._is_status(s.title)]
        if len(status_sections) <= self.keep_most_recent:
            result.skipped_reason = (
                f"only {len(status_sections)} status block(s); nothing to archive"
            )
            return result

        # Keep the first N (newest), archive the rest.
        # Convention: notes are written newest-first, so the earliest sections
        # are the most recent. If your repo writes oldest-first, pass
        # keep_most_recent and adjust the call site, or reverse the order.
        to_archive = status_sections[self.keep_most_recent:]
        to_keep = status_sections[: self.keep_most_recent]

        archive_blocks: list[str] = []
        for sec in to_archive:
            content = sec.body
            if self.summarize is not None:
                try:
                    content = self.summarize(sec.body)
                    result.summaries.append(content)
                except Exception:  # noqa: BLE001
                    # Fall back to verbatim if the summarizer fails.
                    pass
            archive_blocks.append(content)

        archived_text = "\n\n".join(archive_blocks)
        result.archived_sections = len(to_archive)
        result.archived_tokens = estimate_tokens(archived_text)

        # Build the new file content: drop archived sections, insert pointer.
        archive_path = _archive_path_for(path)
        new_text = _rebuild_with_pointer(
            text=text, sections=sections, drop=to_archive,
            archive_relname=archive_path.name,
            kept_count=len(to_keep),
        )
        result.archive_path = archive_path
        result.compacted_tokens = estimate_tokens(new_text)

        if not dry_run:
            archive_path.parent.mkdir(parents=True, exist_ok=True)
            existing_archive = ""
            if archive_path.exists():
                existing_archive = archive_path.read_text(encoding="utf-8")
            archive_header = f"# Status archive — {path.stem}\n\n"
            archive_path.write_text(
                archive_header + archived_text +
                ("\n\n---\n\n" + existing_archive if existing_archive else "\n"),
                encoding="utf-8",
            )
            path.write_text(new_text, encoding="utf-8")

        return result

    def _is_status(self, title: str) -> bool:
        return any(p.search(title) for p in self.patterns)


# -- helpers -------------------------------------------------------------


def parse_sections(text: str) -> list[Section]:
    """Split markdown text into Section objects by ATX-style headers.

    A section runs from one header through to the byte-before the next header
    of the same or shallower level. (Sub-sections are nested *within* their
    parent's body in this model — keeps the slicing simple.)
    """
    matches = list(_HEADER_RE.finditer(text))
    if not matches:
        return []

    sections: list[Section] = []
    for i, m in enumerate(matches):
        level = len(m.group(1))
        title = m.group(2).strip()
        start = m.start()
        # End at the next header of equal-or-shallower level.
        end = len(text)
        for j in range(i + 1, len(matches)):
            other_level = len(matches[j].group(1))
            if other_level <= level:
                end = matches[j].start()
                break
        sections.append(Section(
            level=level, title=title, start=start, end=end,
            body=text[start:end].rstrip() + "\n",
        ))
    return sections


def _rebuild_with_pointer(
    text: str,
    sections: list[Section],
    drop: list[Section],
    archive_relname: str,
    kept_count: int,
) -> str:
    """Reconstruct file with dropped sections replaced by a pointer."""
    drop_ranges = [(s.start, s.end) for s in drop]
    drop_ranges.sort()

    pieces: list[str] = []
    cursor = 0
    pointer = (
        f"\n> _Older status blocks compacted to "
        f"[`{archive_relname}`](./{archive_relname}). "
        f"Showing the {kept_count} most recent here._\n\n"
    )
    pointer_inserted = False

    for start, end in drop_ranges:
        if cursor < start:
            pieces.append(text[cursor:start])
        if not pointer_inserted:
            pieces.append(pointer)
            pointer_inserted = True
        cursor = end
    if cursor < len(text):
        pieces.append(text[cursor:])

    out = "".join(pieces)
    # Collapse runs of >2 blank lines.
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out


def _archive_path_for(path: Path) -> Path:
    """``index.md`` → ``index.archive.md`` next to it."""
    return path.with_name(f"{path.stem}.archive{path.suffix}")

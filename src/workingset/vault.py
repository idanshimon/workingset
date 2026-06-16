"""Vault — represents a folder of markdown notes with frontmatter.

A vault is just a directory. workingset doesn't care if it's an Obsidian vault,
a customer-hub repo, or an agent-os tree — anything with .md files works.

The Vault class handles:
- discovery (walking the tree, respecting .gitignore-style ignores)
- frontmatter parsing (YAML between --- markers at the top of a file)
- "branch" identification (a top-level folder = a branch, e.g. cust/hca/)
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional

import yaml


# Default ignore patterns — directories we never want to index.
DEFAULT_IGNORES: tuple[str, ...] = (
    ".git",
    ".obsidian",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".workingset",  # our own state dir
    ".DS_Store",
    "dist",
    "build",
    ".next",
    ".cache",
)

# Frontmatter is YAML between two --- lines at the very top of a file.
_FRONTMATTER_RE = re.compile(
    r"^---\s*\n(.*?)\n---\s*\n(.*)$",
    re.DOTALL,
)


@dataclass
class Note:
    """A single markdown note in a vault.

    Lazy: ``content`` and ``frontmatter`` are loaded on first access.
    """

    path: Path
    """Absolute path to the .md file."""

    relpath: str
    """Path relative to vault root, forward-slash-normalized."""

    branch: str
    """Top-level folder under vault root (e.g. ``cust/hca`` for
    ``cust/hca/context/index.md``). Empty string for files at the root.
    Used for branch-cache routing."""

    size_bytes: int
    """File size on disk."""

    mtime_ns: int
    """Modification time in ns. Used for incremental reindex."""

    _content: Optional[str] = field(default=None, repr=False)
    _frontmatter: Optional[dict] = field(default=None, repr=False)
    _body: Optional[str] = field(default=None, repr=False)

    @property
    def content(self) -> str:
        """Full file contents (frontmatter + body)."""
        if self._content is None:
            self._content = self.path.read_text(encoding="utf-8", errors="replace")
        return self._content

    @property
    def frontmatter(self) -> dict:
        """Parsed YAML frontmatter, or empty dict if none."""
        if self._frontmatter is None:
            self._parse()
        return self._frontmatter or {}

    @property
    def body(self) -> str:
        """Markdown body without frontmatter."""
        if self._body is None:
            self._parse()
        return self._body or ""

    @property
    def title(self) -> str:
        """Best-effort title: frontmatter ``title``, then first H1, then filename."""
        fm = self.frontmatter
        if isinstance(fm.get("title"), str) and fm["title"].strip():
            return fm["title"].strip()
        for line in self.body.splitlines():
            if line.startswith("# "):
                return line[2:].strip()
            if line.strip():
                break
        return self.path.stem.replace("_", " ").replace("-", " ").title()

    def _parse(self) -> None:
        text = self.content
        m = _FRONTMATTER_RE.match(text)
        if not m:
            self._frontmatter = {}
            self._body = text
            return
        try:
            self._frontmatter = yaml.safe_load(m.group(1)) or {}
            if not isinstance(self._frontmatter, dict):
                # Lists or scalars in frontmatter are weird; ignore them.
                self._frontmatter = {}
        except yaml.YAMLError:
            self._frontmatter = {}
        self._body = m.group(2)


class Vault:
    """A directory of markdown notes."""

    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        ignores: tuple[str, ...] = DEFAULT_IGNORES,
        extensions: tuple[str, ...] = (".md", ".markdown"),
    ) -> None:
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise FileNotFoundError(f"Vault root is not a directory: {self.root}")
        self._ignores = set(ignores)
        self._extensions = tuple(e.lower() for e in extensions)

    @property
    def name(self) -> str:
        """Vault name = directory basename."""
        return self.root.name

    @property
    def state_dir(self) -> Path:
        """Where workingset writes its index + briefs. ``<root>/.workingset/``."""
        d = self.root / ".workingset"
        d.mkdir(exist_ok=True)
        return d

    def walk(self) -> Iterator[Note]:
        """Yield every markdown note under root, in lexicographic order.

        Skips hidden directories and anything in ``ignores``.
        """
        for dirpath, dirnames, filenames in os.walk(self.root):
            # Mutate dirnames in place to skip ignored / hidden dirs.
            dirnames[:] = sorted(
                d for d in dirnames
                if d not in self._ignores and not d.startswith(".")
            )
            for fname in sorted(filenames):
                if not fname.lower().endswith(self._extensions):
                    continue
                if fname.startswith("."):
                    continue
                fpath = Path(dirpath) / fname
                try:
                    stat = fpath.stat()
                except OSError:
                    continue
                rel = fpath.relative_to(self.root).as_posix()
                yield Note(
                    path=fpath,
                    relpath=rel,
                    branch=_branch_for(rel),
                    size_bytes=stat.st_size,
                    mtime_ns=stat.st_mtime_ns,
                )

    def get(self, relpath: str) -> Optional[Note]:
        """Load a single note by its vault-relative path."""
        full = self.root / relpath
        if not full.is_file():
            return None
        stat = full.stat()
        return Note(
            path=full,
            relpath=Path(relpath).as_posix(),
            branch=_branch_for(relpath),
            size_bytes=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
        )

    def branches(self) -> list[str]:
        """Distinct branch identifiers, sorted."""
        seen: set[str] = set()
        for note in self.walk():
            if note.branch:
                seen.add(note.branch)
        return sorted(seen)


def _branch_for(relpath: str) -> str:
    """Compute branch identifier for a relpath.

    Branch = the first two path segments when the first segment is a known
    "carrier" folder (cust, customers, accounts, projects), otherwise just
    the first segment. Files at root return "".

    Examples:
        cust/hca/context/index.md           -> "cust/hca"
        wiki/msft/meeting-notes/foo.md      -> "wiki"
        00-meta/log/2026-06-15.md           -> "00-meta"
        README.md                           -> ""
    """
    parts = Path(relpath).parts
    if len(parts) <= 1:
        return ""
    carriers = {"cust", "customers", "accounts", "projects", "clients"}
    if parts[0] in carriers and len(parts) >= 2:
        return f"{parts[0]}/{parts[1]}"
    return parts[0]

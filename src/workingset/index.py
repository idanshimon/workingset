"""VaultIndex — SQLite FTS5 inverted index over a vault.

The L2 + L3 layers from ContextForge / Codebase-Memory:
- L2: in-SQLite BM25 inverted index, sub-millisecond keyword routing
- L3: WAL-mode SQLite store, effectively unbounded

Index lives at ``<vault>/.workingset/index.db``. Rebuild is incremental —
only files whose mtime changed are reindexed. A full rebuild on a 1300-note
vault takes ~1-2 seconds on commodity hardware; an incremental refresh is
typically <100ms.

Query path returns ``SearchResult`` objects with relpath, title, branch,
score, and a snippet — enough to show the user what matched without loading
file contents.
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from .tokens import estimate_tokens
from .vault import Note, Vault


# FTS5 schema. The content table mirrors filesystem state; the FTS table
# is a virtual table that indexes the searchable columns.
_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS notes (
    relpath        TEXT PRIMARY KEY,
    branch         TEXT NOT NULL,
    title          TEXT NOT NULL,
    body           TEXT NOT NULL,
    frontmatter    TEXT NOT NULL,        -- JSON
    token_estimate INTEGER NOT NULL,
    size_bytes     INTEGER NOT NULL,
    mtime_ns       INTEGER NOT NULL,
    indexed_at     INTEGER NOT NULL      -- unix seconds
);

CREATE INDEX IF NOT EXISTS idx_notes_branch ON notes(branch);
CREATE INDEX IF NOT EXISTS idx_notes_mtime  ON notes(mtime_ns);

-- FTS5 virtual table. ``content=''`` makes it a contentless table —
-- we manage the storage in ``notes`` and feed FTS5 explicitly.
-- prefix='2 3 4' lets prefix queries (renewal*) hit the index.
CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    relpath UNINDEXED,
    branch  UNINDEXED,
    title,
    body,
    tokenize='porter unicode61',
    prefix='2 3 4'
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass
class SearchResult:
    """One match from the BM25 query."""

    relpath: str
    branch: str
    title: str
    score: float
    """BM25 rank (lower = better in raw FTS5; we negate for sort)."""

    snippet: str
    """Highlighted excerpt from the body (FTS5 ``snippet()`` output)."""

    token_estimate: int


@dataclass
class IndexStats:
    """Snapshot of index state."""

    note_count: int
    total_tokens: int
    branches: int
    db_size_bytes: int
    last_indexed_at: Optional[int]


class VaultIndex:
    """SQLite FTS5 index over a Vault.

    Use as a context manager or call ``.close()`` explicitly.
    """

    def __init__(self, vault: Vault) -> None:
        self.vault = vault
        self.db_path = vault.state_dir / "index.db"
        self._conn: Optional[sqlite3.Connection] = None

    def open(self) -> None:
        if self._conn is not None:
            return
        self._conn = sqlite3.connect(self.db_path)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "VaultIndex":
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self.open()
        assert self._conn is not None
        return self._conn

    # -- indexing ---------------------------------------------------------

    def reindex(self, *, full: bool = False) -> tuple[int, int, int]:
        """Walk the vault and update the index.

        Returns ``(added, updated, removed)`` counts. By default, only files
        whose mtime changed are touched. Pass ``full=True`` to drop the
        existing index first.
        """
        self.open()
        if full:
            self.conn.execute("DELETE FROM notes_fts")
            self.conn.execute("DELETE FROM notes")
            self.conn.commit()

        # Snapshot of what's currently indexed.
        existing: dict[str, int] = dict(
            self.conn.execute("SELECT relpath, mtime_ns FROM notes").fetchall()
        )
        seen: set[str] = set()
        added = updated = 0

        now = int(time.time())
        for note in self.vault.walk():
            seen.add(note.relpath)
            prev_mtime = existing.get(note.relpath)
            if prev_mtime == note.mtime_ns:
                continue  # unchanged

            self._upsert(note, indexed_at=now)
            if prev_mtime is None:
                added += 1
            else:
                updated += 1

        # Anything we used to have but didn't see this walk → delete.
        stale = [rp for rp in existing if rp not in seen]
        if stale:
            placeholders = ",".join("?" * len(stale))
            self.conn.execute(
                f"DELETE FROM notes_fts WHERE relpath IN ({placeholders})", stale
            )
            self.conn.execute(
                f"DELETE FROM notes WHERE relpath IN ({placeholders})", stale
            )

        self._set_meta("last_indexed_at", str(now))
        self.conn.commit()
        return added, updated, len(stale)

    def _upsert(self, note: Note, *, indexed_at: int) -> None:
        title = note.title
        body = note.body
        fm = json.dumps(note.frontmatter, default=str)
        toks = estimate_tokens(note.content)

        self.conn.execute("DELETE FROM notes_fts WHERE relpath = ?", (note.relpath,))
        self.conn.execute(
            """
            INSERT INTO notes (relpath, branch, title, body, frontmatter,
                               token_estimate, size_bytes, mtime_ns, indexed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(relpath) DO UPDATE SET
                branch=excluded.branch,
                title=excluded.title,
                body=excluded.body,
                frontmatter=excluded.frontmatter,
                token_estimate=excluded.token_estimate,
                size_bytes=excluded.size_bytes,
                mtime_ns=excluded.mtime_ns,
                indexed_at=excluded.indexed_at
            """,
            (note.relpath, note.branch, title, body, fm, toks,
             note.size_bytes, note.mtime_ns, indexed_at),
        )
        self.conn.execute(
            "INSERT INTO notes_fts (relpath, branch, title, body) VALUES (?, ?, ?, ?)",
            (note.relpath, note.branch, title, body),
        )

    # -- query ------------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        top_k: int = 10,
        branch: Optional[str] = None,
        min_score: float = 0.0,
    ) -> list[SearchResult]:
        """BM25 keyword search.

        Args:
            query: User query. Passed to FTS5 with light sanitization;
                multi-word queries match documents containing all terms.
            top_k: Maximum results.
            branch: If given, restrict to one branch (e.g. ``"cust/acme"``).
            min_score: Drop results whose normalized score is below this.
        """
        self.open()
        clean = _sanitize_fts(query)
        if not clean:
            return []

        sql = """
            SELECT n.relpath, n.branch, n.title, n.token_estimate,
                   bm25(notes_fts) AS rank,
                   snippet(notes_fts, 3, '<<', '>>', ' … ', 16) AS snip
            FROM notes_fts
            JOIN notes n ON n.relpath = notes_fts.relpath
            WHERE notes_fts MATCH ?
        """
        params: list = [clean]
        if branch:
            sql += " AND n.branch = ?"
            params.append(branch)
        sql += " ORDER BY rank LIMIT ?"
        params.append(top_k)

        rows = self.conn.execute(sql, params).fetchall()
        results: list[SearchResult] = []
        for relpath, br, title, toks, rank, snip in rows:
            # FTS5 bm25() returns negative numbers — more negative = better.
            # Flip so higher = better and clip to >= 0 for sanity.
            score = -float(rank)
            if score < min_score:
                continue
            results.append(SearchResult(
                relpath=relpath, branch=br or "", title=title,
                score=score, snippet=snip, token_estimate=toks,
            ))
        return results

    # -- working set assembly --------------------------------------------

    def working_set(
        self,
        query: str,
        *,
        budget_tokens: int = 8000,
        branch: Optional[str] = None,
        boost_branches: Optional[Iterable[str]] = None,
        boost_factor: float = 1.5,
    ) -> tuple[list[SearchResult], int]:
        """Assemble a working set for ``query`` under a token budget.

        Returns ``(selected, total_tokens)``. Greedy packs by score, with
        a 1.5× boost (ContextForge convention) for any result whose branch
        is in ``boost_branches`` — useful for "active customer this week"
        biasing.
        """
        boost = set(boost_branches or [])
        candidates = self.search(query, top_k=50, branch=branch)
        if boost:
            for c in candidates:
                if c.branch in boost:
                    c.score *= boost_factor
            candidates.sort(key=lambda c: -c.score)

        selected: list[SearchResult] = []
        used = 0
        for c in candidates:
            if used + c.token_estimate > budget_tokens and selected:
                break
            selected.append(c)
            used += c.token_estimate
        return selected, used

    # -- stats ------------------------------------------------------------

    def stats(self) -> IndexStats:
        self.open()
        n, toks = self.conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(token_estimate), 0) FROM notes"
        ).fetchone()
        b = self.conn.execute("SELECT COUNT(DISTINCT branch) FROM notes").fetchone()[0]
        last = self._get_meta("last_indexed_at")
        size = self.db_path.stat().st_size if self.db_path.exists() else 0
        return IndexStats(
            note_count=int(n),
            total_tokens=int(toks),
            branches=int(b),
            db_size_bytes=size,
            last_indexed_at=int(last) if last else None,
        )

    # -- meta helpers -----------------------------------------------------

    def _set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    def _get_meta(self, key: str) -> Optional[str]:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None


_SAFE_RE = None  # reserved


def _sanitize_fts(query: str) -> str:
    """Make a user query safe for FTS5 MATCH.

    Strategy: split on whitespace, quote each token to neutralize FTS5
    operators (NEAR, AND, OR, NOT, parentheses, *), then AND them. This
    accepts free-form queries without surprising the user with operator
    semantics. Power users can call ``conn.execute`` directly for raw
    FTS5 syntax.
    """
    tokens = [t for t in query.split() if t.strip()]
    if not tokens:
        return ""
    quoted: list[str] = []
    for t in tokens:
        # Strip surrounding quotes the user added; we'll re-add them.
        t = t.strip("\"'")
        # Escape internal double-quotes per FTS5 quoting rules ("" = literal ").
        t = t.replace('"', '""')
        quoted.append(f'"{t}"')
    return " ".join(quoted)

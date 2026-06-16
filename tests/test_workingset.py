"""End-to-end test against a temporary vault. The smoke test that proves
the four primitives — index, search, brief, compact — actually work.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from workingset.brief import BriefGenerator, SectionBudget, _trim_lines_to_budget
from workingset.compact import StatusCompactor, parse_sections
from workingset.index import VaultIndex
from workingset.tokens import estimate_tokens
from workingset.vault import Vault, _branch_for


def _seed_vault(root: Path) -> None:
    """Create a tiny vault that mimics customer-hub shape."""
    (root / "cust" / "acme").mkdir(parents=True)
    (root / "cust" / "acme" / "index.md").write_text(
        "---\ncustomer: acme\ntpid: 12345\n---\n"
        "# Acme — overview\n\n## 🔥 STATUS (June 15, 2026)\n"
        "Latest: Acme signed the deal. Owner: Idan.\n\n"
        "## 🔥 STATUS (June 1, 2026)\nEarlier: contract negotiations.\n\n"
        "## 🔥 STATUS (May 14, 2026)\nKickoff happened.\n\n"
        "## Action items\n- [ ] Schedule QBR with Acme leadership\n"
        "- [x] Send signed MSA\n- [ ] Loop in legal on data terms\n",
        encoding="utf-8",
    )
    (root / "cust" / "acme" / "personas.md").write_text(
        "# Personas\n\n## Jane Doe — CIO\nDecision maker. "
        "Owner: Idan. Due: 2026-07-01.\n\n## Bob Smith — Architect\n"
        "Technical reviewer.\n",
        encoding="utf-8",
    )
    (root / "wiki" / "internal").mkdir(parents=True)
    (root / "wiki" / "internal" / "glossary.md").write_text(
        "# Glossary\n\n## Foundry\nAzure AI Foundry, MSFT model catalog.\n",
        encoding="utf-8",
    )


def test_branch_inference():
    assert _branch_for("cust/acme/context/index.md") == "cust/acme"
    assert _branch_for("wiki/internal/glossary.md") == "wiki"
    assert _branch_for("README.md") == ""


def test_vault_walk(tmp_path: Path):
    _seed_vault(tmp_path)
    v = Vault(tmp_path)
    notes = list(v.walk())
    assert len(notes) == 3
    rels = sorted(n.relpath for n in notes)
    assert rels == [
        "cust/acme/index.md",
        "cust/acme/personas.md",
        "wiki/internal/glossary.md",
    ]


def test_frontmatter_parsing(tmp_path: Path):
    _seed_vault(tmp_path)
    v = Vault(tmp_path)
    note = v.get("cust/acme/index.md")
    assert note is not None
    assert note.frontmatter == {"customer": "acme", "tpid": 12345}
    assert note.title == "Acme — overview"
    assert "STATUS" in note.body


def test_index_and_search(tmp_path: Path):
    _seed_vault(tmp_path)
    v = Vault(tmp_path)
    with VaultIndex(v) as ix:
        added, updated, removed = ix.reindex(full=True)
        assert added == 3
        assert updated == 0
        assert removed == 0

        # Searching 'acme' should rank the cust/acme notes above wiki.
        results = ix.search("acme")
        assert len(results) >= 1
        top = results[0]
        assert top.relpath.startswith("cust/acme/")

        # Branch-restricted search.
        scoped = ix.search("acme", branch="cust/acme")
        assert all(r.branch == "cust/acme" for r in scoped)


def test_incremental_reindex(tmp_path: Path):
    _seed_vault(tmp_path)
    v = Vault(tmp_path)
    with VaultIndex(v) as ix:
        ix.reindex(full=True)
        # No changes → all zeros.
        a, u, r = ix.reindex()
        assert (a, u, r) == (0, 0, 0)

        # Modify a file → exactly one update.
        # We need a real mtime change; sleep + re-touch to be safe.
        import time
        time.sleep(0.01)
        (tmp_path / "cust" / "acme" / "index.md").write_text(
            "# Acme — overview (revised)\n\nUpdated.\n",
            encoding="utf-8",
        )
        a, u, r = ix.reindex()
        assert u == 1


def test_working_set_budget(tmp_path: Path):
    _seed_vault(tmp_path)
    v = Vault(tmp_path)
    with VaultIndex(v) as ix:
        ix.reindex(full=True)
        # Tiny budget should still return at least one result (the spec
        # always keeps the top hit even if it'd exceed).
        sel, used = ix.working_set("acme", budget_tokens=5)
        assert len(sel) >= 1


def test_brief_generation(tmp_path: Path):
    _seed_vault(tmp_path)
    v = Vault(tmp_path)
    with VaultIndex(v) as ix:
        ix.reindex(full=True)
        b = BriefGenerator(v, ix, budget_tokens=2000).for_branch("cust/acme")

    # Core invariants.
    assert "workingset-brief" in b.content
    assert "cust/acme" in b.content
    assert b.stats.notes_indexed == 2
    # We should have surfaced at least one open action item and one heading.
    assert b.stats.action_items_kept >= 1
    assert b.stats.headings_kept >= 1


def test_brief_for_empty_branch(tmp_path: Path):
    _seed_vault(tmp_path)
    v = Vault(tmp_path)
    with VaultIndex(v) as ix:
        ix.reindex(full=True)
        b = BriefGenerator(v, ix).for_branch("cust/nonexistent")
    assert "no notes yet" in b.content


def test_section_parsing():
    text = "# A\n\nbody A\n\n## A1\n\nsub\n\n# B\n\nbody B\n"
    secs = parse_sections(text)
    titles = [s.title for s in secs]
    assert titles == ["A", "A1", "B"]
    # Section A spans up to the next H1 (B), so it includes A1's content.
    a = secs[0]
    assert "body A" in a.body
    assert "sub" in a.body


def test_compact_under_threshold(tmp_path: Path):
    p = tmp_path / "small.md"
    p.write_text("# small\n\n## STATUS (today)\nfine\n", encoding="utf-8")
    cmp = StatusCompactor(threshold_tokens=10_000)
    r = cmp.compact(p)
    assert not r.did_compact
    assert r.skipped_reason and "threshold" in r.skipped_reason


def test_compact_archives_old_blocks(tmp_path: Path):
    p = tmp_path / "big.md"
    # Build a file with several status blocks. Each block ~400 tok of filler
    # so we cross a low threshold.
    blocks = []
    filler = "lorem ipsum dolor sit amet " * 100
    for i, label in enumerate(["June 15", "June 1", "May 14", "April 1"]):
        blocks.append(f"## 🔥 STATUS ({label})\n{filler}\n")
    p.write_text("# Project\n\n" + "\n".join(blocks), encoding="utf-8")

    pre_tokens = estimate_tokens(p.read_text())
    cmp = StatusCompactor(threshold_tokens=500, keep_most_recent=1)
    r = cmp.compact(p)
    assert r.did_compact
    assert r.archived_sections == 3
    assert r.compacted_tokens < pre_tokens
    # Archive file should have been written next to the original.
    assert r.archive_path is not None and r.archive_path.exists()
    assert "Status archive" in r.archive_path.read_text()
    # Pointer should appear in the rewritten file.
    rewritten = p.read_text()
    assert "compacted to" in rewritten
    # And the most recent block should still be inline.
    assert "STATUS (June 15)" in rewritten


# -- Regression tests for the postmortem fixes -----------------------------


def test_decisions_no_double_bullet(tmp_path: Path):
    """Bug from the first run: indented bullet lines surfaced as `- - **Owner**`.

    The decision regex used to drop a leading `- ` then re-emit with another
    `- `, producing visible double-dashes. After the fix, output should
    have exactly one bullet per decision line.
    """
    (tmp_path / "cust" / "x").mkdir(parents=True)
    (tmp_path / "cust" / "x" / "notes.md").write_text(
        "# X\n\n## Plan\n"
        "- **Owner (MSFT):** Don Dinulos / ISD + Lim Ko.\n"
        "- **Action:** Paul leading enablement session.\n"
        "- **Decision:** Day 2 demos removed from agenda.\n",
        encoding="utf-8",
    )
    v = Vault(tmp_path)
    with VaultIndex(v) as ix:
        ix.reindex(full=True)
        b = BriefGenerator(v, ix, budget_tokens=2000).for_branch("cust/x")

    # No double-bullet anywhere in the output.
    assert "- - " not in b.content, (
        "Double-bullet leaked into brief output:\n" + b.content
    )
    # All three decision lines should still be there.
    assert "Don Dinulos" in b.content
    assert "Paul leading" in b.content
    assert "Day 2 demos" in b.content


def test_decisions_skip_action_items():
    """Decision regex shouldn't double-emit lines that are also checkboxes.

    `- [ ] **Action:** foo` is an action item — it goes in the actions
    section. The decisions section should not also list it.
    """
    text = (
        "## Plan\n"
        "- [ ] **Action:** Send the invite\n"
        "- **Owner:** Idan\n"
        "- [x] **Action:** Already done\n"
    )
    # We test the BriefGenerator end-to-end rather than poking the regex
    # directly — the regex catches the line, the assembly should reject it.
    from pathlib import Path as P
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        root = P(td)
        (root / "cust" / "y").mkdir(parents=True)
        (root / "cust" / "y" / "n.md").write_text(text, encoding="utf-8")
        v = Vault(root)
        with VaultIndex(v) as ix:
            ix.reindex(full=True)
            b = BriefGenerator(v, ix, budget_tokens=2000).for_branch("cust/y")

    # The action lines should appear in actions, the owner in decisions.
    assert b.stats.action_items_kept >= 1
    # And the decision section must not contain "[ ]" or "[x]" tokens —
    # those belong only to the actions section.
    decision_section = ""
    in_dec = False
    for line in b.content.splitlines():
        if line.startswith("## Recent decisions"):
            in_dec = True
            continue
        if in_dec and line.startswith("## "):
            break
        if in_dec:
            decision_section += line + "\n"
    assert "[ ]" not in decision_section and "[x]" not in decision_section


def test_per_section_budget_keeps_recent_titles(tmp_path: Path):
    """Pre-fix bug: a fat actions section consumed the global budget,
    causing the trailing "Most recent notes" section to be silently
    truncated. Per-section budgets should preserve the bottom section
    even when the top is full.
    """
    (tmp_path / "cust" / "z").mkdir(parents=True)
    # 30 long action items.
    actions = "\n".join(
        f"- [ ] **[Owner-{i}]** {'lorem ipsum dolor ' * 20} #{i}"
        for i in range(30)
    )
    (tmp_path / "cust" / "z" / "actions.md").write_text(
        "# Z actions\n\n## Open\n" + actions,
        encoding="utf-8",
    )
    # Plus 5 other notes so "Most recent notes" has > 1 entry.
    for i in range(5):
        (tmp_path / "cust" / "z" / f"note{i}.md").write_text(
            f"# Note {i}\n\ncontent\n", encoding="utf-8",
        )

    v = Vault(tmp_path)
    with VaultIndex(v) as ix:
        ix.reindex(full=True)
        b = BriefGenerator(v, ix, budget_tokens=600).for_branch("cust/z")

    # The trailing "Most recent notes" section MUST be present.
    assert "## Most recent notes" in b.content, (
        "Most recent notes section was silently dropped:\n" + b.content
    )
    # The truncation marker from the old behavior must NOT appear.
    assert "_(truncated to fit budget)_" not in b.content


def test_section_budget_allocation():
    sb = SectionBudget()
    alloc = sb.allocate(1000)
    # Sum of allocations should be roughly the input (we have a small
    # overhead reservation).
    total = sum(alloc.values())
    assert 800 <= total <= 1000
    # No section gets less than its floor.
    assert alloc["actions"] >= 60
    assert alloc["decisions"] >= 40
    assert alloc["headings"] >= 50
    assert alloc["recent"] >= 40


def test_trim_lines_to_budget_helper():
    # 20 long-ish lines should NOT all fit in a 30-token budget.
    lines = [
        f"- [ ] **[Owner-{i}]** {'lorem ipsum ' * 10} task #{i}"
        for i in range(20)
    ]
    kept, trimmed = _trim_lines_to_budget(lines, budget=30)
    assert trimmed
    assert len(kept) < len(lines)
    # Joined kept lines fit in the budget.
    assert estimate_tokens("\n".join(kept)) <= 30

    # Empty input returns empty without trimming.
    kept2, trimmed2 = _trim_lines_to_budget([], budget=100)
    assert kept2 == []
    assert trimmed2 is False

    # When everything fits, trimmed=False.
    kept3, trimmed3 = _trim_lines_to_budget(["a", "b"], budget=10_000)
    assert kept3 == ["a", "b"]
    assert trimmed3 is False

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
    """Create a tiny vault that mimics a customer-notes vault shape."""
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
        "- **Owner (Vendor):** Alice Chen / Platform + Bob Patel.\n"
        "- **Action:** Carol leading enablement session.\n"
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
    assert "Alice Chen" in b.content
    assert "Carol leading" in b.content
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
    alloc = sb.allocate(10_000)
    # Allocations should leave room for the 5% overhead bucket.
    total = sum(alloc.values())
    assert 9_000 <= total <= 10_500  # status+actions+decisions+headings+recent
    # No section gets less than its floor.
    assert alloc["actions"] >= 200
    assert alloc["status"] >= 300
    assert alloc["decisions"] >= 100
    assert alloc["headings"] >= 100
    assert alloc["recent"] >= 80
    # Status section is at least as big as decisions (it's higher-priority).
    assert alloc["status"] >= alloc["decisions"]


def test_status_block_extracted_into_brief(tmp_path: Path):
    """The most recent ## 🔥 STATUS block should appear verbatim in the brief.

    This is the highest-signal content for status-block-heavy vaults and was
    missing from the brief in v1. Regression test.
    """
    from workingset.brief import _extract_latest_status

    (tmp_path / "cust" / "acme").mkdir(parents=True)
    (tmp_path / "cust" / "acme" / "index.md").write_text(
        "# Acme\n\n"
        "## 🔥 STATUS (June 15, 2026): KICKOFF DONE | SCOPE LOCKED\n\n"
        "**Just landed:** scope corrected, two-act framing locked, "
        "8 attendees confirmed.\n"
        "- Workshop invite gap caught\n"
        "- Legacy-system sleeper post-workshop\n\n"
        "## 🔥 STATUS (May 22, 2026): EARLIER\n\n"
        "Old status content.\n\n"
        "## Other section\n\nUnrelated.\n",
        encoding="utf-8",
    )
    v = Vault(tmp_path)
    notes = sorted(v.walk(), key=lambda n: -n.mtime_ns)

    # Extractor finds the FIRST status block (notes already sorted newest-first).
    block, src = _extract_latest_status(notes)
    assert block is not None
    assert "June 15, 2026" in block
    assert "KICKOFF DONE" in block
    assert "Workshop invite gap caught" in block
    # It stops at the next status header, doesn't bleed into May 22.
    assert "Old status content" not in block
    assert "## Other section" not in block
    assert src == "cust/acme/index.md"

    with VaultIndex(v) as ix:
        ix.reindex(full=True)
        b = BriefGenerator(v, ix, budget_tokens=4000).for_branch("cust/acme")

    # Status block must surface in the brief content + stats.
    assert "## Latest status (verbatim)" in b.content
    assert "KICKOFF DONE" in b.content
    assert b.stats.status_block_included
    assert b.stats.status_source == "cust/acme/index.md"


def test_status_extractor_returns_none_when_absent():
    """No STATUS blocks → no status section, no crash."""
    from workingset.brief import _extract_latest_status
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "cust" / "x").mkdir(parents=True)
        (root / "cust" / "x" / "n.md").write_text(
            "# Plain note\n\n## Section\n\nNo status here.\n",
            encoding="utf-8",
        )
        v = Vault(root)
        notes = list(v.walk())
        block, src = _extract_latest_status(notes)
        assert block is None
        assert src is None

        with VaultIndex(v) as ix:
            ix.reindex(full=True)
            b = BriefGenerator(v, ix, budget_tokens=2000).for_branch("cust/x")
        assert "## Latest status" not in b.content
        assert b.stats.status_block_included is False


def test_brief_does_not_read_itself(tmp_path: Path):
    """Regression: brief.md must NOT be ingested as a source note.

    First run produced a brief; second run was treating brief.md as input,
    leading to triplicated `_[cust/x/brief.md]_` source tags and a
    self-referential status_source. The brief generator must filter out
    its own artifacts.
    """
    (tmp_path / "cust" / "z").mkdir(parents=True)
    (tmp_path / "cust" / "z" / "index.md").write_text(
        "# Z\n\n## 🔥 STATUS (June 1)\nReal status content.\n\n"
        "## Plan\n- [ ] Real action item\n",
        encoding="utf-8",
    )
    v = Vault(tmp_path)

    # First run.
    with VaultIndex(v) as ix:
        ix.reindex(full=True)
        b1 = BriefGenerator(v, ix, budget_tokens=4000).for_branch("cust/z")
        b1.write(tmp_path / "cust" / "z" / "brief.md")
        ix.reindex()  # pick up the new file

        # Second run — should NOT see brief.md as a source.
        b2 = BriefGenerator(v, ix, budget_tokens=4000).for_branch("cust/z")

    # The status source should still point at index.md, not brief.md.
    assert b2.stats.status_source == "cust/z/index.md", (
        f"status_source leaked to brief artifact: {b2.stats.status_source}"
    )
    # No action item should reference brief.md as a source.
    assert "_[cust/z/brief.md]_" not in b2.content, (
        "brief.md leaked into source tags:\n" + b2.content
    )
    # And source_notes count should not have inflated.
    assert b2.stats.notes_indexed == 1


def test_status_trim_keeps_content_under_tight_budget():
    """Regression: status trimmer used to drop everything below the header
    when budget was tight, leaving just '## STATUS ...' + '_(trimmed)_'.

    Now we hard-cut the first paragraph instead — a header without body
    is useless.
    """
    from workingset.brief import _trim_block_to_budget

    block = (
        "## 🔥 STATUS (June 15): KICKOFF DONE | SCOPE LOCKED\n\n"
        "**Just landed (vendor + customer team):**\n"
        + ("- Workshop invite gap caught\n" * 30)
        + "- Legacy-system sleeper post-workshop\n\n"
        "Old paragraph 1.\n\n"
        "Old paragraph 2.\n"
    )

    # Budget too small to fit even the first paragraph in full.
    out = _trim_block_to_budget(block, budget=80)
    # Header is preserved.
    assert "KICKOFF DONE" in out
    # We never return JUST the header — there's some content + a marker.
    assert "_(" in out  # one of the trimmed markers
    # We didn't return the entire block.
    assert len(out) < len(block)


def test_status_trim_no_op_when_under_budget():
    from workingset.brief import _trim_block_to_budget
    block = "## STATUS (today)\n\nshort content.\n"
    assert _trim_block_to_budget(block, budget=10_000) == block


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

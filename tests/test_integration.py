"""End-to-end integration tests.

These exercise the full pipeline against the canonical example vault
shipped at examples/example-vault/. Unlike the unit tests in
test_workingset.py (which build tmp_path vaults inline), these run
against real on-disk content that ships with the repo, so they verify
both the code AND the example fixtures stay in sync.

If you change the example vault, expect these tests to need updating.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

EXAMPLE_VAULT = Path(__file__).parent.parent / "examples" / "example-vault"


@pytest.fixture
def vault(tmp_path):
    """Copy the example vault to tmp_path so tests don't pollute the source tree."""
    if not EXAMPLE_VAULT.is_dir():
        pytest.skip("example vault not present; only ships with workingset repo")
    dst = tmp_path / "vault"
    shutil.copytree(EXAMPLE_VAULT, dst)
    # Wipe any pre-existing index or brief from the example
    if (dst / ".workingset").exists():
        shutil.rmtree(dst / ".workingset")
    for brief in dst.rglob("brief.md"):
        brief.unlink()
    return dst


def ws(*args, cwd, capture_json=False):
    """Run the `ws` CLI as a subprocess and return (stdout, exit_code)."""
    cmd = [sys.executable, "-m", "workingset.cli", *args]
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return result.stdout, result.stderr, result.returncode


def test_full_pipeline_init_query_brief_diff(vault):
    """Full happy path: init → query → brief → diff.

    Exercises every primary CLI command in sequence against a real fixture
    vault. Verifies the pipeline produces a brief, that the brief is smaller
    than the source, and that the index supports BM25 search.
    """
    # 1. init builds the FTS5 index
    out, err, rc = ws("init", cwd=vault)
    assert rc == 0, f"init failed: {err}"
    assert (vault / ".workingset" / "index.db").exists(), \
        "init should create .workingset/index.db"
    assert "5 notes" in out, f"expected 5 notes indexed, got: {out!r}"

    # 2. query returns ranked branches under the budget
    out, err, rc = ws("query", "renewal call", "-B", "4000", "--branch", "cust/acme", cwd=vault)
    assert rc == 0, f"query failed: {err}"
    assert "cust/acme" in out, "query should return the cust/acme branch"

    # 3. brief writes <branch>/brief.md
    out, err, rc = ws("brief", "cust/acme", "--budget", "8000", "--write", cwd=vault)
    assert rc == 0, f"brief failed: {err}"
    brief_path = vault / "cust" / "acme" / "brief.md"
    assert brief_path.exists(), "brief should write to <branch>/brief.md"
    brief_text = brief_path.read_text()

    # Brief must have YAML frontmatter
    assert brief_text.startswith("---\n"), "brief must start with frontmatter"
    assert "kind: workingset-brief" in brief_text
    assert "version: 2" in brief_text

    # Brief must include the verbatim STATUS block
    assert "🔥 STATUS" in brief_text, "brief should include the verbatim STATUS block"
    assert "RENEWAL CALL DONE" in brief_text, "brief should preserve STATUS content"

    # Brief must include action items
    assert "Open action items" in brief_text
    assert "[Vendor — URGENT]" in brief_text, "URGENT action item must surface"

    # 4. diff measures the savings
    out, err, rc = ws("diff", "cust/acme", cwd=vault)
    assert rc == 0, f"diff failed: {err}"
    assert "Before" in out and "After" in out
    assert "reduction" in out.lower() or "saved" in out.lower()


def test_brief_does_not_ingest_itself_on_reindex(vault):
    """Regression: after writing brief.md, re-running init+brief must NOT
    re-ingest brief.md as a source note. Symptom would be tripled source
    tags or self-referential status_source frontmatter."""
    # First pass
    ws("init", cwd=vault)
    ws("brief", "cust/acme", "--budget", "8000", "--write", cwd=vault)

    # Second pass — should NOT include brief.md as a source
    ws("reindex", cwd=vault)
    ws("brief", "cust/acme", "--budget", "8000", "--write", cwd=vault)

    brief = (vault / "cust" / "acme" / "brief.md").read_text()
    # No self-referential source tags
    assert "_[cust/acme/brief.md]_" not in brief, \
        "brief.md should never appear as its own source"
    # status_source must point to a context file, never to brief.md itself
    fm_lines = brief.split("---", 2)[1]
    assert "status_source: cust/acme/brief.md" not in fm_lines, \
        "status_source must not be self-referential"


def test_json_output_is_valid(vault):
    """Every CLI command with --json should emit valid JSON wrapped in a
    schema-version envelope.

    Envelope shape (since v0.4):
        {"schema_version": "X.Y", "command": "<name>", "data": {...}}
    """
    ws("init", cwd=vault)
    # query --json
    out, err, rc = ws("query", "stakeholder", "--branch", "cust/acme", "--json", cwd=vault)
    assert rc == 0
    envelope = json.loads(out)
    assert "schema_version" in envelope, "json output must have schema_version envelope"
    assert "command" in envelope and envelope["command"] == "query"
    data = envelope["data"]
    assert isinstance(data, dict)
    assert "results" in data or "branches" in data or "query" in data, \
        f"query data should produce structured results, got: {data}"

    # stats --json
    out, err, rc = ws("stats", "--json", cwd=vault)
    assert rc == 0
    envelope = json.loads(out)
    assert envelope["command"] == "stats"
    stats = envelope["data"]
    assert "notes" in stats or "branches" in stats, \
        f"stats data should produce structured info, got: {stats}"


def test_brief_respects_budget(vault):
    """Generated brief must respect the --budget flag (within reason — the
    STATUS block is verbatim and can push past budget slightly, but should
    never be 2x over)."""
    ws("init", cwd=vault)
    ws("brief", "cust/acme", "--budget", "4000", "--write", cwd=vault)
    brief = (vault / "cust" / "acme" / "brief.md").read_text()
    # rough token estimate: chars/4
    est_tokens = len(brief) // 4
    # 4000 budget, allow up to 50% overhead for verbatim status block
    assert est_tokens < 6000, \
        f"brief at --budget 4000 produced ~{est_tokens} tokens, expected < 6000"


def test_version_flag():
    """`ws --version` should report the installed version."""
    out, err, rc = ws("--version", cwd=EXAMPLE_VAULT if EXAMPLE_VAULT.exists() else Path.cwd())
    assert rc == 0
    assert "ws" in out.lower() or "version" in out.lower(), \
        f"--version output should mention 'ws' or 'version', got: {out!r}"
    # Should match the installed __version__
    import workingset
    assert workingset.__version__ in out, \
        f"--version should print {workingset.__version__}, got: {out!r}"

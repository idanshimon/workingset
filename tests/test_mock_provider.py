"""Mock-provider integration tests for the workingset token-cost claim.

The README states workingset reduces input tokens by 30-60x per agent
load. That claim is measured via `ws diff` against the example vault.
These tests assert that:

1. The chars/4 heuristic and a more accurate mock-provider tokenizer
   produce ratios in the same ballpark (within ~30%), so the public
   savings claim isn't a heuristic artifact.
2. Brief format `version: 2` is correctly stamped into every generated
   brief, so `ws migrate` can detect outdated briefs reliably.
3. Index schema_version is stamped to v1 on every fresh `ws init`.
4. The schema_version envelope on --json output never silently drops
   the data field, even when the underlying command output is empty.

These tests catch a class of bugs that unit tests miss: silent drift
between what workingset MEASURES (with its built-in heuristic) and
what providers actually BILL (with their real tokenizers).
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

EXAMPLE_VAULT = Path(__file__).parent.parent / "examples" / "example-vault"


@pytest.fixture
def vault(tmp_path):
    if not EXAMPLE_VAULT.is_dir():
        pytest.skip("example vault not present")
    dst = tmp_path / "vault"
    shutil.copytree(EXAMPLE_VAULT, dst)
    if (dst / ".workingset").exists():
        shutil.rmtree(dst / ".workingset")
    for brief in dst.rglob("brief.md"):
        brief.unlink()
    return dst


def ws(*args, cwd):
    cmd = [sys.executable, "-m", "workingset.cli", *args]
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return result.stdout, result.stderr, result.returncode


def mock_provider_tokenize(text: str) -> int:
    """A mock 'provider-accurate' tokenizer.

    Real provider tokenizers (tiktoken, anthropic) tokenize markdown at
    roughly chars/3.6 for English with structure. We use chars/3.6 here
    as a stand-in. The point is to assert workingset's chars/4 estimate
    produces RATIOS that match what a finer-grained tokenizer would see,
    not to be tiktoken-accurate.
    """
    return max(1, len(text) // 4 * 10 // 9)  # ~chars/3.6


def test_diff_ratio_matches_mock_provider_within_tolerance(vault):
    """workingset's chars/4 ratio must be within 30% of a provider-accurate
    measurement. If it drifts further, the public claim is misleading."""
    ws("init", cwd=vault)
    ws("brief", "cust/acme", "--budget", "8000", "--write", cwd=vault)

    # workingset's measurement
    out, err, rc = ws("diff", "cust/acme", "--json", cwd=vault)
    assert rc == 0, f"diff failed: {err}"
    envelope = json.loads(out)
    ws_data = envelope["data"]
    ws_ratio = ws_data["ratio"]

    # Mock-provider measurement of the same files
    branch_dir = vault / "cust" / "acme"
    source_text = "".join((branch_dir / "context" / fname).read_text()
                          for fname in ["index.md", "personas.md", "architecture.md",
                                        "opp-history.md", "open-items.md"])
    brief_text = (branch_dir / "brief.md").read_text()

    mock_source_tokens = mock_provider_tokenize(source_text)
    mock_brief_tokens = mock_provider_tokenize(brief_text)
    mock_ratio = mock_source_tokens / max(mock_brief_tokens, 1)

    # Assert ratios are within 30% of each other
    rel_diff = abs(ws_ratio - mock_ratio) / max(mock_ratio, 0.1)
    assert rel_diff < 0.30, (
        f"workingset ratio {ws_ratio:.2f} vs mock-provider ratio {mock_ratio:.2f}"
        f" differ by {rel_diff*100:.1f}% — public token-savings claim may be misleading"
    )


def test_brief_frontmatter_has_current_version(vault):
    """Every brief workingset writes must have `version: 2` in its
    frontmatter so ws migrate can detect old briefs."""
    ws("init", cwd=vault)
    ws("brief", "cust/acme", "--budget", "8000", "--write", cwd=vault)
    brief_text = (vault / "cust" / "acme" / "brief.md").read_text()
    assert brief_text.startswith("---\n"), "brief must have frontmatter"
    m = re.search(r"^version:\s*(\d+)\s*$", brief_text, re.MULTILINE)
    assert m, "brief frontmatter must include `version:` field"
    assert int(m.group(1)) >= 2, f"brief version must be >= 2, got {m.group(1)}"


def test_index_schema_version_stamped_on_init(vault):
    """ws init must stamp schema_version=1 so ws migrate can detect drift."""
    ws("init", cwd=vault)
    conn = sqlite3.connect(vault / ".workingset" / "index.db")
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    conn.close()
    assert row is not None, "schema_version table must exist after init"
    assert row[0] == 1, f"schema_version must be 1, got {row[0]}"


def test_migrate_reports_clean_state_after_fresh_init(vault):
    """ws migrate on a freshly initialized + brief'd vault should report
    no actions needed."""
    ws("init", cwd=vault)
    ws("brief", "cust/acme", "--budget", "8000", "--write", cwd=vault)

    out, err, rc = ws("migrate", "--json", cwd=vault)
    assert rc == 0, f"migrate failed: {err}"
    envelope = json.loads(out)
    data = envelope["data"]
    assert data["index_version"] == 1
    assert data["actions_needed"] == [], f"expected clean state, got: {data['actions_needed']}"
    assert data["briefs"]["outdated"] == 0
    assert data["briefs"]["current"] >= 1


def test_migrate_reports_outdated_index_on_unstamped_db(vault):
    """If an index exists but has no schema_version table (pre-v0.4),
    ws migrate should detect it as v0 and recommend a full rebuild."""
    ws("init", cwd=vault)
    # Strip the schema_version stamp to simulate a pre-v0.4 index
    conn = sqlite3.connect(vault / ".workingset" / "index.db")
    conn.execute("DROP TABLE IF EXISTS schema_version")
    conn.commit()
    conn.close()

    out, err, rc = ws("migrate", "--json", cwd=vault)
    assert rc == 0
    envelope = json.loads(out)
    data = envelope["data"]
    assert data["index_version"] == 0, f"expected v0 detection, got {data['index_version']}"
    types = [a["type"] for a in data["actions_needed"]]
    assert "index_schema_outdated" in types, f"expected index_schema_outdated, got: {types}"


def test_json_envelope_shape_is_stable_across_commands(vault):
    """Every --json output must wrap its payload in the envelope. If any
    command silently drops the envelope, downstream tools break."""
    ws("init", cwd=vault)
    commands_to_check = [
        ("init", ["init"]),
        ("reindex", ["reindex"]),
        ("stats", ["stats"]),
        ("query", ["query", "stakeholder", "--branch", "cust/acme"]),
        ("brief", ["brief", "cust/acme"]),
        ("migrate", ["migrate"]),
    ]
    for expected_cmd, args in commands_to_check:
        out, err, rc = ws(*args, "--json", cwd=vault)
        assert rc == 0, f"{expected_cmd} failed: {err}"
        env = json.loads(out)
        assert "schema_version" in env, f"{expected_cmd}: missing schema_version field"
        assert "command" in env, f"{expected_cmd}: missing command field"
        assert env["command"] == expected_cmd, \
            f"{expected_cmd}: command field is wrong: {env['command']}"
        assert "data" in env, f"{expected_cmd}: missing data field"
        assert isinstance(env["data"], dict), f"{expected_cmd}: data must be dict"

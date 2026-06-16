# Changelog

All notable changes to `workingset` are documented here.
This project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- `LICENSE` file (MIT) at repo root
- `CONTRIBUTING.md` with PR conventions and dev workflow
- `CHANGELOG.md` (this file)
- `.github/workflows/test.yml` — CI on pushes/PRs: pytest matrix on Python 3.11 / 3.12 / 3.13, coverage upload
- `docs/architecture.md` — 5-layer mapping (ContextForge → workingset translation)
- `docs/cli-reference.md` — exhaustive CLI reference, generated from `ws --help` + hand-edited
- `docs/adoption-guide.md` — step-by-step recipe for wiring workingset into a new vault
- `ws --version` flag
- `examples/example-vault/` — canonical fixture vault anyone can point `ws` at to see a real brief
- Integration tests (`tests/test_integration.py`) covering the full `init → reindex → query → brief → diff` pipeline end-to-end
- `pytest --cov` configuration; coverage reports in `coverage.xml` + terminal

### Changed
- Bumped version to `0.3.0` to reflect Tier-1 + Tier-2 framework maturity work
- README now links to all `docs/` entries

## [0.2.0] — 2026-06-15

### Added
- Default `--budget` raised from 500 → 8,000 tokens after user feedback that 1,500 was "not enough" for customer-hub-shaped vaults
- Verbatim `## 🔥 STATUS (date)` block extraction into briefs (highest-signal-density content)
- `ws diff` measures on-disk `brief.md` by default; `--include` flag for file-glob comparisons
- Optional `tiktoken` support (`ws diff --tokenizer tiktoken`) for provider-accurate token counts (default remains the model-agnostic `chars/4` heuristic)

### Fixed
- **Brief self-ingestion:** `cust/<slug>/brief.md` is no longer ingested as a source note on the next regeneration. Symptom was triplicated `_[brief.md]_` source tags + self-referential `status_source` frontmatter.
- **Status-block trim returning header-only:** under tight budgets, trim now hard-cuts the first content paragraph rather than dropping everything below the header. A header without body is useless.
- **Decision-regex double-bullet:** `- **Owner:** Bob` used to surface as `- - **Owner:** Bob`. Regex now strips a leading list marker before re-emitting. Also rejects checkbox lines so they don't double-count with actions.
- **Per-section budget vs global truncation:** `SectionBudget` dataclass governs per-section allocation; the trailing "Most recent notes" section is no longer silently dropped under global truncation.

### Scrubbed
- All customer-identifying names (HCA, Kapil, Don Dinulos, BizTalk, etc.) removed from README, source docstrings, test fixtures, and CLI examples. Replaced with generic `cust/acme` / fictional contacts. See commit `9c7766f`.

## [0.1.0] — 2026-06-14

### Added
- Initial release. Walks a folder of markdown, builds a SQLite FTS5 index, produces per-folder briefs.
- Core CLI: `ws init / reindex / stats / query / brief / compact / diff`
- 5-layer ContextForge-derived architecture (L0 residual brief, L1 branch cache, L2 SQLite FTS5 index, L3 filesystem vault)
- 21 unit tests covering vault walk, frontmatter parsing, index build, BM25 query, brief generation, status compaction
- README with honest measurement framing (real-tokenizer 34× reduction on customer-notes vault, postmortem of earlier wrong 72× / 114× claims)
- Pre-built `cust/acme` brief shape: frontmatter + verbatim STATUS + open actions + decisions + topics + recent notes

[Unreleased]: https://github.com/idanshimon/workingset/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/idanshimon/workingset/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/idanshimon/workingset/releases/tag/v0.1.0

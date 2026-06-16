# Contributing to workingset

Thanks for considering a contribution. workingset is a small, focused tool;
keeping it that way is part of the value proposition. Read this before
opening a PR.

## Philosophy

- **Small and honest.** ~600 LOC of real logic. Every dependency, every
  layer, every flag has to earn its place. If a feature can be done with
  a 10-line user script, it probably should be.
- **Measure first.** Don't add anything without a failing test that
  demonstrates the gap, and a benchmark (or `ws diff` measurement) showing
  the change moves a real number.
- **Document the gap as part of the fix.** Pitfalls go in `docs/` or the
  `references/` of the matching skill. The bug catalog is part of the deliverable.

## Development setup

```bash
git clone https://github.com/idanshimon/workingset
cd workingset
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,llm]"
pytest tests/ -v
```

All 21 unit tests + ~6 integration tests should pass in under 5 seconds.

## Running the test suite

```bash
# fast feedback loop
pytest tests/ -q

# with coverage
pytest tests/ --cov=workingset --cov-report=term-missing

# integration tests only
pytest tests/test_integration.py -v

# a specific test
pytest tests/test_workingset.py::test_status_block_extracted_into_brief -v
```

CI runs the full matrix on Python 3.11, 3.12, 3.13. Your PR will be checked
automatically.

## What's in scope

- Bug fixes (especially regressions on documented pitfalls)
- New vault shapes (`Vault(extra_carriers=...)` for non-customer-notes layouts)
- New section extractors (currently: status / actions / decisions / topics / recent)
- Performance improvements with measurements attached
- Documentation, especially for new use cases
- Real-vault integration recipes

## What's out of scope

- LLM-based summarization beyond the optional `--llm` flag (the markdown layer
  is the value; deferring to an LLM at every step defeats the purpose)
- Embedding-based retrieval (`--hybrid` etc.) without a failing test from a
  real query — BM25 + FTS5 handles entity-heavy queries; embeddings add
  complexity for unclear gain
- Web UI / GUI (CLI + library is the surface)
- Auto-publishing to PyPI (install-from-source is deliberate for now)

## Pull request conventions

1. **One concern per PR.** Mixing a refactor + a feature + a bugfix makes
   review impossible.
2. **Tests with new code.** Every behavior change needs a test. Bug fixes
   need a regression test that fails on `main` and passes with the fix.
3. **Update docs.** If your change touches the CLI surface, update
   `docs/cli-reference.md`. If it changes architecture, update
   `docs/architecture.md`.
4. **Update CHANGELOG.md** under `[Unreleased]` with a one-line entry.
5. **Run the pre-flight check:**
   ```bash
   pytest tests/ && python3 -c "import workingset; print(workingset.__version__)"
   ```
6. **Commit messages:** imperative mood, present tense, ~70 chars on the
   subject. Example:
   ```
   Fix decision-regex double-bullet on checkbox lines

   The decision parser was matching `- [ ] Owner: Bob` and emitting it
   into the decisions section as `- - [ ] Owner: Bob`. Reject lines that
   match the checkbox pattern before applying the decision regex.

   Regression test: test_decisions_skip_action_items.
   ```

## Reporting bugs

Open a GitHub issue with:

- `workingset --version` output
- A minimal vault that reproduces (or steps to reproduce on an existing vault)
- Expected behavior vs actual behavior
- The actual `brief.md` output if relevant (or `ws diff` numbers)

If the bug involves a specific kind of markdown content (a STATUS block
shape, a frontmatter convention, etc.), include a minimal example file.

## Reporting wrong measurements

The README's headline number (34×) is measured on one real vault. If you
run `ws diff` on your vault and the ratio is dramatically different
(positive or negative), open an issue or PR with the measurement so we
can document the range of real-world behavior. Honest numbers matter
more than impressive ones.

## License

By contributing, you agree your work is licensed under MIT (the project's
license). See [LICENSE](LICENSE).

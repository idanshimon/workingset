# bench/

Reproducible behavioral benchmark for the workingset claim:
*"workingset reduces input tokens by 30-60× per agent load, with 0% accuracy
loss on questions whose answer is in the brief, and 0% hallucination on
out-of-brief questions for frontier-tier models."*

This folder is the public, self-contained version of the 432-trial study
that produced the [benchmark article](https://github.com/idanshimon/workingset).
It points at [`examples/example-vault/`](../examples/example-vault) so
anyone who clones the repo can re-run the benchmark on the same fixture
the maintainers measured against.

## TL;DR

```bash
cd bench
python3 harness/run_minimal_bench.py    # ~3 min, ~$0.50 in Copilot credits
python3 harness/render_report.py        # produces report.html
open report.html
```

## What this bench actually does

For each model in `MODELS`, and each question in `QUESTIONS`:

1. Load context **two ways**:
   - `brief_only`: read `examples/example-vault/cust/acme/brief.md`
   - `full_source`: cat all 5 files under `examples/example-vault/cust/acme/context/`
2. Ask the question via the model
3. Grade the response: `correct` / `abstained` / `hallucinated`
4. Record input/output tokens from the model's billing footer

Then compare:

- **Token cost**: how many fewer input tokens did `brief_only` cost vs `full_source`?
- **Accuracy**: did the model still answer the brief-contained questions correctly?
- **Hallucination rate**: did the model invent answers when the brief didn't contain them?

## The questions

Three categories, all answerable from the example vault:

| ID | Question | In brief? | Correct answer |
|---|---|---|---|
| Q1 | What is the workshop date? | Yes | July 14-15 (Atlanta) |
| Q2 | Who is the primary technical buyer? | Yes | Priya Sharma |
| Q3 | How many in-house developers does Acme have? | **No** (trap) | 3,200-3,500 (only in `architecture.md`, NOT brief) |

**Q3 is the trap question.** The brief mentions "Priya's 10-20 core
engineers" in a different context (workshop attendance). A model that
confuses workshop-attendance with developer-headcount will hallucinate
"10-20" as the answer. Frontier models recognize this and abstain; small
models occasionally don't.

## Reproducing the published 432-trial number

The full benchmark in the article tested 9 LLMs × 2 conditions × 8
questions × 3 runs = 432 trials. This bundled bench is a simpler subset
(3 questions, fewer models, 1 run) to keep cost / time reasonable for
public reproducibility. To run the full version:

```bash
python3 harness/run_minimal_bench.py --models all --questions full --runs 3
```

Expect ~$15 in Copilot credits and ~60 min wall time.

## Requirements

- `copilot` CLI installed and authenticated (this is the model-routing
  layer — workingset itself doesn't talk to any LLM, the bench does)
- Python 3.11+
- The `examples/example-vault/` fixture (ships with this repo)

## What this is NOT

- **Not a guarantee about your data.** This bench measures behavior on
  the example vault. Your own customer-notes will have a different ratio
  and a different trap-question profile. Use this as a methodology
  template, not a value claim about your vault.
- **Not exhaustive.** Three questions can't cover every failure mode.
  Production deployments should add their own trap questions to the
  harness.
- **Not a model leaderboard.** The benchmark exists to test
  *workingset's* claim, not to rank model quality. A model that scores
  100% here might fail catastrophically on a different vault shape.

## See also

- [Full benchmark article](https://github.com/idanshimon/workingset) —
  the 432-trial study, methodology, and the multi-run finding that
  single-run benchmarks miss
- [`docs/architecture.md`](../docs/architecture.md) — what workingset is
  doing under the hood
- [`docs/adoption-guide.md`](../docs/adoption-guide.md) — including the
  safety section on trap-question failure modes

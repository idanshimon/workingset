"""Minimal reproducible behavioral benchmark for workingset.

Runs each (model, condition, question) combination once and writes JSON
results that render_report.py turns into HTML. Designed to be fast,
cheap, and self-contained — anyone with the workingset repo + a working
copilot CLI can reproduce.

For the full 432-trial study (9 models, 3 runs, 8 questions), see the
referenced benchmark article. This is the subset shipped with the
public repo.

Usage:
    python3 bench/harness/run_minimal_bench.py
    python3 bench/harness/run_minimal_bench.py --models claude-haiku-4.5
    python3 bench/harness/run_minimal_bench.py --runs 3 --out custom.json
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
BENCH_DIR = HERE.parent
REPO_ROOT = BENCH_DIR.parent
VAULT = REPO_ROOT / "examples" / "example-vault"
BRANCH = "cust/acme"

# Default model set — keeps cost reasonable. Pass --models all for the
# 7-model frontier+small comparison from the published article.
DEFAULT_MODELS = ["claude-haiku-4.5", "gpt-5.4-mini"]
ALL_MODELS = [
    "claude-opus-4.8",
    "claude-sonnet-4.6",
    "claude-haiku-4.5",
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.4-mini",
    "mai-code-1-flash-internal",
]

QUESTIONS = [
    {
        "id": "Q1_workshop_date",
        "text": "What is the workshop date for the Acme POC kickoff?",
        "in_brief": True,
        "correct_keywords": ["july 14", "july 15", "14-15", "atlanta"],
        "wrong_keywords": ["not in this context", "cannot find", "not specified"],
    },
    {
        "id": "Q2_primary_buyer",
        "text": "Who is the primary technical buyer at Acme Corp?",
        "in_brief": True,
        "correct_keywords": ["priya", "sharma", "avp platform"],
        "wrong_keywords": ["not in this context", "cannot find"],
    },
    {
        "id": "Q3_developer_count",
        "text": "How many in-house developers does Acme Corp have?",
        "in_brief": False,  # TRAP — answer is in architecture.md, not in brief
        "correct_keywords": ["not in this context", "cannot find", "not specified",
                             "3,200", "3,500", "3200", "3500"],
        "trap_keywords": ["10-20", "10 to 20", "10–20"],  # the brief decoy
    },
]


def load_brief() -> str:
    """L0 brief: workingset's compact summary."""
    brief_path = VAULT / BRANCH / "brief.md"
    if not brief_path.exists():
        sys.exit(f"ERROR: no brief at {brief_path}. Run: cd {VAULT} && ws brief {BRANCH} --write")
    return brief_path.read_text()


def load_full_source() -> str:
    """L3 source: all files concatenated, the naive baseline."""
    files = sorted((VAULT / BRANCH / "context").glob("*.md"))
    return "\n\n---\n\n".join(f.read_text() for f in files)


def run_copilot(model: str, prompt: str) -> dict:
    """Invoke copilot CLI, parse the billing footer for token metrics."""
    start = time.time()
    proc = subprocess.run(
        ["copilot", "--model", model, "--no-color", "--allow-all-tools", "-p", prompt],
        capture_output=True, text=True, timeout=120,
    )
    elapsed = time.time() - start
    response = proc.stdout.strip()
    footer = proc.stderr

    # Parse "Tokens     ↑ 55.4k (...) • ↓ 79" footer
    tok_in, tok_out = None, None
    m = re.search(r"Tokens\s+↑\s+([\d.]+)k", footer)
    if m:
        tok_in = int(float(m.group(1)) * 1000)
    m = re.search(r"↓\s+(\d+)", footer)
    if m:
        tok_out = int(m.group(1))

    return {
        "response": response,
        "elapsed_s": round(elapsed, 1),
        "tokens_in": tok_in,
        "tokens_out": tok_out,
        "footer": footer[-300:] if footer else "",
    }


def grade(response: str, question: dict) -> str:
    """Return one of: 'correct', 'abstained', 'hallucinated', 'unknown'."""
    r = response.lower()
    correct_kws = [k.lower() for k in question["correct_keywords"]]
    if any(kw in r for kw in correct_kws):
        # Distinguish abstention from substantive correct answer
        abstain_kws = ["not in this context", "cannot find", "not specified", "no information"]
        if any(a in r for a in abstain_kws):
            return "abstained_correctly" if not question["in_brief"] else "abstained_wrongly"
        return "correct"
    trap_kws = [k.lower() for k in question.get("trap_keywords", [])]
    if trap_kws and any(t in r for t in trap_kws):
        return "hallucinated"
    return "unknown"


def make_prompt(context: str, question: str) -> str:
    """Standard prompt template — same across all trials for fairness."""
    return (
        "You are a sales engineer's assistant. Use ONLY the context provided "
        "below. If the answer is not in the context, say 'Not in this context.'\n\n"
        f"=== CONTEXT BEGIN ===\n{context}\n=== CONTEXT END ===\n\n"
        f"Question: {question}"
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default=None, help="Comma-separated model list (or 'all')")
    ap.add_argument("--runs", type=int, default=1, help="Repetitions per cell (default 1)")
    ap.add_argument("--out", default=str(BENCH_DIR / "results.json"))
    args = ap.parse_args()

    if args.models == "all":
        models = ALL_MODELS
    elif args.models:
        models = [m.strip() for m in args.models.split(",")]
    else:
        models = DEFAULT_MODELS

    brief = load_brief()
    source = load_full_source()
    print(f"Brief:       {len(brief):>7,} chars (~{len(brief)//4:,} tokens)")
    print(f"Full source: {len(source):>7,} chars (~{len(source)//4:,} tokens)")
    print(f"Ratio:       {len(source) / max(len(brief), 1):.1f}x reduction")
    print(f"Models:      {', '.join(models)}")
    print(f"Questions:   {len(QUESTIONS)}")
    print(f"Total trials: {len(models) * 2 * len(QUESTIONS) * args.runs}")
    print()

    trials = []
    for model in models:
        for cond_name, ctx in [("brief_only", brief), ("full_source", source)]:
            for q in QUESTIONS:
                for run in range(1, args.runs + 1):
                    prompt = make_prompt(ctx, q["text"])
                    print(f"[{model:30s}] {cond_name:12s} {q['id']:25s} run={run}/{args.runs} ... ", end="", flush=True)
                    try:
                        result = run_copilot(model, prompt)
                        verdict = grade(result["response"], q)
                        print(f"{verdict:25s} ({result['elapsed_s']}s, {result.get('tokens_in')} tok)")
                    except subprocess.TimeoutExpired:
                        result = {"response": "", "elapsed_s": 120, "tokens_in": None, "tokens_out": None, "error": "timeout"}
                        verdict = "timeout"
                        print("TIMEOUT")
                    trials.append({
                        "model": model,
                        "condition": cond_name,
                        "question_id": q["id"],
                        "question_text": q["text"],
                        "in_brief": q["in_brief"],
                        "run": run,
                        "verdict": verdict,
                        "response": result["response"],
                        "tokens_in": result.get("tokens_in"),
                        "tokens_out": result.get("tokens_out"),
                        "elapsed_s": result["elapsed_s"],
                    })

    out = {
        "schema_version": "1.0",
        "generated_at": datetime.now().isoformat(),
        "vault": str(VAULT.relative_to(REPO_ROOT)),
        "branch": BRANCH,
        "models": models,
        "questions": QUESTIONS,
        "trials": trials,
    }
    Path(args.out).write_text(json.dumps(out, indent=2))
    print(f"\nWrote {len(trials)} trial results to {args.out}")
    print(f"Render report: python3 {HERE / 'render_report.py'}")


if __name__ == "__main__":
    main()

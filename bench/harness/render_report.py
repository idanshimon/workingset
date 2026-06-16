"""Render bench/results.json into a self-contained HTML report.

Inputs:  bench/results.json (or whatever --in path is passed)
Outputs: bench/report.html

Designed to match the dark-themed aesthetic of the full multi-run
dashboard but stripped down to the essentials for the public/reproducible
bench. No JavaScript — pure HTML/CSS/SVG.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from statistics import median

HERE = Path(__file__).parent
BENCH_DIR = HERE.parent

DARK_CSS = """
* { box-sizing: border-box; }
body { background: #0a0e0d; color: #d4e3df; font-family: -apple-system, sans-serif;
       font-size: 16px; line-height: 1.6; max-width: 900px; margin: 0 auto; padding: 36px 24px; }
h1 { font-size: 1.8em; letter-spacing: -0.4px; }
h2 { font-size: 1.2em; border-top: 1px solid #1f2d2f; padding-top: 24px; margin-top: 36px; }
code { background: #060a09; color: #6dd47b; padding: 1px 6px; border-radius: 3px;
       font-family: ui-monospace, monospace; font-size: 0.88em; }
.meta { color: #7d9590; font-family: ui-monospace, monospace; font-size: 0.85em;
        margin-bottom: 26px; padding-bottom: 18px; border-bottom: 1px solid #1f2d2f; }
table { width: 100%; border-collapse: collapse; margin: 16px 0; font-size: 0.92em; }
th, td { padding: 8px 12px; border-bottom: 1px solid #1f2d2f; text-align: left; }
th { color: #6dd47b; font-size: 0.78em; text-transform: uppercase; letter-spacing: 0.5px; }
td.num { font-family: ui-monospace, monospace; text-align: right; }
.win { color: #6dd47b; }
.bad { color: #e07474; }
.warn { color: #e8b860; }
.dim { color: #7d9590; }
.callout { background: #131c1f; border-left: 3px solid #6dd47b; padding: 16px 20px;
           margin: 20px 0; border-radius: 0 4px 4px 0; }
.callout.bad { border-left-color: #e07474; }
.callout.warn { border-left-color: #e8b860; }
"""


def render(data: dict, out_path: Path) -> None:
    trials = data["trials"]
    models = data["models"]
    questions = data["questions"]

    # Per-cell aggregates
    cells = defaultdict(list)
    for t in trials:
        cells[(t["model"], t["condition"], t["question_id"])].append(t)

    # Token savings per model
    token_savings = []
    for m in models:
        brief_toks = [t["tokens_in"] for t in trials
                      if t["model"] == m and t["condition"] == "brief_only" and t["tokens_in"]]
        source_toks = [t["tokens_in"] for t in trials
                       if t["model"] == m and t["condition"] == "full_source" and t["tokens_in"]]
        if brief_toks and source_toks:
            token_savings.append({
                "model": m,
                "brief_median": int(median(brief_toks)),
                "source_median": int(median(source_toks)),
                "saved": int(median(source_toks) - median(brief_toks)),
                "ratio": median(source_toks) / max(median(brief_toks), 1),
            })

    # Verdict counts per (model, condition)
    verdict_counts = defaultdict(lambda: defaultdict(int))
    for t in trials:
        verdict_counts[(t["model"], t["condition"])][t["verdict"]] += 1

    parts = ["<!doctype html><html><head>",
             '<meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width,initial-scale=1">',
             "<title>workingset bench report</title>",
             f"<style>{DARK_CSS}</style></head><body>"]

    parts.append('<div class="meta">workingset bench · '
                 f'{data["generated_at"][:19]} · '
                 f'{len(trials)} trials · {len(models)} models</div>')
    parts.append("<h1>workingset behavioral benchmark report</h1>")
    parts.append('<p class="dim">Reproducible behavioral check of the workingset claim: '
                 f'<code>brief.md</code> replaces source loading at significant token savings '
                 f'with no accuracy loss on in-brief questions and 0% hallucination on '
                 f'out-of-brief questions for frontier-tier models.</p>')

    # Section 1: token savings
    parts.append("<h2>01 What workingset saved</h2>")
    if token_savings:
        parts.append("<table><thead><tr><th>Model</th><th>full_source (tok)</th>"
                     "<th>brief_only (tok)</th><th>Saved</th><th>Ratio</th></tr></thead><tbody>")
        for s in token_savings:
            parts.append(f"<tr><td><code>{s['model']}</code></td>"
                         f"<td class='num bad'>{s['source_median']:,}</td>"
                         f"<td class='num win'>{s['brief_median']:,}</td>"
                         f"<td class='num win'>+{s['saved']:,}</td>"
                         f"<td class='num'>{s['ratio']:.1f}x</td></tr>")
        parts.append("</tbody></table>")
    else:
        parts.append("<p class='dim'>No token metrics captured (older copilot CLI?)</p>")

    # Section 2: verdict matrix
    parts.append("<h2>02 Behavioral verdicts</h2>")
    parts.append("<p class='dim'>Per (model, condition): how many trials resulted in each verdict.</p>")
    parts.append("<table><thead><tr><th>Model</th><th>Condition</th>"
                 "<th class='win'>Correct</th><th class='dim'>Abstained (correctly)</th>"
                 "<th class='bad'>Hallucinated</th><th>Other</th></tr></thead><tbody>")
    for m in models:
        for cond in ["brief_only", "full_source"]:
            counts = verdict_counts[(m, cond)]
            correct = counts.get("correct", 0)
            abstained = counts.get("abstained_correctly", 0)
            halluc = counts.get("hallucinated", 0) + counts.get("abstained_wrongly", 0)
            other = sum(counts.values()) - correct - abstained - halluc
            parts.append(f"<tr><td><code>{m}</code></td><td class='dim'>{cond}</td>"
                         f"<td class='num win'>{correct}</td>"
                         f"<td class='num dim'>{abstained}</td>"
                         f"<td class='num bad'>{halluc}</td>"
                         f"<td class='num'>{other}</td></tr>")
    parts.append("</tbody></table>")

    # Section 3: trap detail
    parts.append("<h2>03 Trap question — verbatim responses</h2>")
    parts.append("<p class='dim'>Q3 asks for the developer count, which is NOT in the brief. "
                 "The brief contains '10-20 core engineers' in a different context (workshop attendance). "
                 "Correct behavior: abstain. Trap behavior: report '10-20' as the answer.</p>")
    trap_trials = [t for t in trials if t["question_id"] == "Q3_developer_count" and t["condition"] == "brief_only"]
    for t in trap_trials:
        verdict_class = {"hallucinated": "bad", "abstained_correctly": "win", "abstained_wrongly": "bad"}.get(t["verdict"], "warn")
        parts.append(f"<div class='callout {verdict_class}'>")
        parts.append(f"<div class='dim'>{t['model']} · run {t['run']} · verdict: <strong>{t['verdict']}</strong></div>")
        resp = t["response"][:400] + ("..." if len(t["response"]) > 400 else "")
        parts.append(f"<div style='font-family:ui-monospace,monospace;font-size:0.9em;margin-top:8px'>{resp}</div>")
        parts.append("</div>")

    parts.append('<p class="dim" style="margin-top:36px;font-size:0.85em">'
                 'Reproduce: <code>cd bench && python3 harness/run_minimal_bench.py && python3 harness/render_report.py</code>'
                 '</p>')
    parts.append("</body></html>")

    out_path.write_text("\n".join(parts))
    print(f"Wrote {len(out_path.read_text())} bytes -> {out_path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", default=str(BENCH_DIR / "results.json"))
    ap.add_argument("--out", default=str(BENCH_DIR / "report.html"))
    args = ap.parse_args()

    data = json.loads(Path(args.in_path).read_text())
    render(data, Path(args.out))


if __name__ == "__main__":
    main()

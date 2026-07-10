#!/usr/bin/env python3
"""Assemble a MEASURED model-comparison table from eval reports (logs/eval-*.json).

The point of cost columns is a business decision: *which model gives enough correctness for the
least money/latency?* To answer that honestly you compare runs that differ in ONE thing — the
answerer — with the judge, dataset, retrieval config and gate all held fixed. Each run already
wrote its answerer + aggregate (correctness, $/req, tokens, p50/p95) to logs/eval-*.json, so this
script just groups those reports by answerer (keeping the latest per model) and renders a table.
Nothing is estimated — every cell traces to a real report.

Run:  python scripts/compare_models.py                       # all logs/eval-*.json
      python scripts/compare_models.py logs/a.json logs/b.json   # explicit set
"""
import os
import sys
import glob
import json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE = 0.80   # correctness pass-rate needed to ship (same gate the eval uses)


def _reports(paths):
    paths = paths or sorted(glob.glob(os.path.join(ROOT, "logs", "eval-*.json")))
    if not paths:
        sys.exit("no eval reports found — run the eval first (writes logs/eval-*.json)")
    return [json.load(open(p)) for p in paths]


def _latest_per_answerer(reports):
    # Reports are keyed by answerer; a later run for the same model supersedes an earlier one.
    # run_id is a sortable timestamp string, so max(run_id) wins.
    by_model = {}
    for r in reports:
        m = r.get("answerer", "?")
        if m not in by_model or r.get("run_id", "") > by_model[m].get("run_id", ""):
            by_model[m] = r
    return by_model


def _row(r):
    a = r["aggregate"]
    corr = a["dims"]["correctness"]
    return {
        "answerer": r.get("answerer", "?"),
        "corr": corr,
        "passed": a.get("passed"), "n": a.get("n"),
        "cost_req": a.get("avg_cost_usd", 0.0),
        "total_cost": a.get("avg_cost_usd", 0.0) * a.get("n", 0),
        "tokens": a.get("total_tokens", 0),
        "p50": a.get("p50_ms", 0) / 1000, "p95": a.get("p95_ms", 0) / 1000,
        "gate": "✅" if corr >= GATE else "❌",
    }


def _fmt(reports):
    by_model = _latest_per_answerer(reports)
    rows = sorted((_row(r) for r in by_model.values()), key=lambda x: -x["corr"])

    # Fairness note: flag if the runs don't actually share dataset / retrieval config.
    datasets = {r.get("dataset") for r in by_model.values()}
    sources = {r.get("retrieval_source") for r in by_model.values()}
    judges = {r.get("judge") for r in by_model.values()}

    out = []
    out.append("| Answerer | Correctness | Pass | $/req | Total $ | Tokens | p50 / p95 | Gate |")
    out.append("|---|---|---|---|---|---|---|---|")
    for x in rows:
        out.append(
            f"| `{x['answerer']}` | **{x['corr'] * 100:.0f}%** | {x['passed']}/{x['n']} | "
            f"${x['cost_req']:.4f} | ${x['total_cost']:.3f} | {x['tokens']:,} | "
            f"{x['p50']:.1f}s / {x['p95']:.1f}s | {x['gate']} |")

    # The decision the cost column exists to support: cheapest model that still passes.
    passing = [x for x in rows if x["corr"] >= GATE]
    if passing:
        best = min(passing, key=lambda x: x["cost_req"])
        out.append("")
        out.append(f"**Cheapest model over the {GATE:.0%} gate:** `{best['answerer']}` — "
                   f"{best['corr'] * 100:.0f}% correct at ${best['cost_req']:.4f}/request.")

    note = f"\n_Held fixed across rows: judge `{'/'.join(sorted(j for j in judges if j))}`, " \
           f"dataset `{'/'.join(sorted(d for d in datasets if d))}`, " \
           f"retrieval `{'/'.join(sorted(s for s in sources if s))}`. Numbers from logs/eval-*.json._"
    if len(datasets) > 1 or len(sources) > 1:
        note += "\n\n⚠️ **Not a clean A/B** — rows differ in dataset or retrieval config above."
    out.append(note)
    return "\n".join(out)


if __name__ == "__main__":
    print(_fmt(_reports(sys.argv[1:])))

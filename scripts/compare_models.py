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


def _group_by_answerer(reports):
    # Keep ALL runs per model (we run each ≥2x to see run-to-run variance on a non-deterministic system).
    by_model = {}
    for r in reports:
        by_model.setdefault(r.get("answerer", "?"), []).append(r)
    return by_model


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def _agg(answerer, reps):
    # Aggregate N runs of one model into mean + range, so the table shows both the central value and
    # how much a single run could have misled us.
    corr = [r["aggregate"]["dims"]["correctness"] for r in reps]
    cost = [r["aggregate"].get("avg_cost_usd", 0.0) for r in reps]
    p50 = [r["aggregate"].get("p50_ms", 0) / 1000 for r in reps]
    p95 = [r["aggregate"].get("p95_ms", 0) / 1000 for r in reps]
    n = reps[0]["aggregate"].get("n", 0)
    return {
        "answerer": answerer, "runs": len(reps),
        "corr_mean": _mean(corr), "corr_lo": min(corr), "corr_hi": max(corr), "n": n,
        "cost_req": _mean(cost), "p50": _mean(p50), "p95": _mean(p95),
        "gate": "✅" if _mean(corr) >= GATE else "❌",
    }


def _fmt(reports):
    by_model = _group_by_answerer(reports)
    rows = sorted((_agg(m, reps) for m, reps in by_model.items()), key=lambda x: -x["corr_mean"])
    all_reps = [r for reps in by_model.values() for r in reps]
    datasets = {r.get("dataset") for r in all_reps}
    sources = {r.get("retrieval_source") for r in all_reps}
    judges = {r.get("judge") for r in all_reps}

    def corr_cell(x):
        base = f"**{x['corr_mean'] * 100:.0f}%**"
        return base if x["corr_lo"] == x["corr_hi"] else \
            f"{base} ({x['corr_lo'] * 100:.0f}–{x['corr_hi'] * 100:.0f})"

    out = ["| Answerer | Correctness (mean, range) | Runs | $/req | p50 / p95 | Gate |",
           "|---|---|---|---|---|---|"]
    for x in rows:
        out.append(f"| `{x['answerer']}` | {corr_cell(x)} | {x['runs']} | ${x['cost_req']:.4f} | "
                   f"{x['p50']:.1f}s / {x['p95']:.1f}s | {x['gate']} |")

    passing = [x for x in rows if x["corr_mean"] >= GATE]
    if passing:
        best = min(passing, key=lambda x: x["cost_req"])
        out += ["", f"**Cheapest model over the {GATE:.0%} gate:** `{best['answerer']}` — "
                    f"{best['corr_mean'] * 100:.0f}% mean at ${best['cost_req']:.4f}/request."]

    note = f"\n_Each model run {rows[0]['runs'] if rows else '?'}×. Held fixed: judge " \
           f"`{'/'.join(sorted(j for j in judges if j))}`, dataset " \
           f"`{'/'.join(sorted(d for d in datasets if d))}`, retrieval " \
           f"`{'/'.join(sorted(s for s in sources if s))}`. Numbers from logs/eval-*.json._"
    if len(datasets) > 1 or len(sources) > 1:
        note += "\n\n⚠️ **Not a clean A/B** — reports differ in dataset or retrieval config."
    out.append(note)
    return "\n".join(out)


if __name__ == "__main__":
    print(_fmt(_reports(sys.argv[1:])))

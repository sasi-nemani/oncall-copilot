#!/usr/bin/env python3
"""Format an eval report (logs/eval-*.json) into a ready-to-paste docs/RUNS.md entry.

The numbers come straight from the report so a journal entry can never drift from the run that
produced it; the "Changed from" / "Finding" lines are left blank for you to write — that judgement
is the part a human adds. Keeps the run journal honest AND cheap to maintain for every future run.

Run:  python scripts/log_run.py logs/eval-<ts>.json
      python scripts/log_run.py                 # uses the most recent logs/eval-*.json
"""
import os
import sys
import glob
import json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _latest_report():
    reports = sorted(glob.glob(os.path.join(ROOT, "logs", "eval-*.json")))
    if not reports:
        sys.exit("no logs/eval-*.json found — run an eval first")
    return reports[-1]


def _fmt(report_path):
    with open(report_path) as f:
        r = json.load(f)
    agg = r.get("aggregate", {})
    dims = agg.get("dims", {})
    correctness = dims.get("correctness")            # 0..1 fractions in the report
    tools = dims.get("tool_choice")
    safety = dims.get("safety")
    passed, total = agg.get("passed"), agg.get("n")
    cost = agg.get("avg_cost_usd")
    tokens = agg.get("total_tokens")
    p50, p95 = agg.get("p50_ms"), agg.get("p95_ms")
    gate = "OPEN" if (correctness or 0) >= 0.80 else "BLOCKED"

    def pct(x):
        return f"{x * 100:.0f}%" if isinstance(x, (int, float)) else "?"

    def secs(ms):
        return f"{ms / 1000:.1f}s" if isinstance(ms, (int, float)) else "?"

    rel = os.path.relpath(report_path, ROOT)
    return f"""## Run N — <one-line title>
*({r.get('run_id', '?')})*

**Config**
```
retrieval : RETRIEVAL_SOURCE={r.get('retrieval_source', '?')}   filter=<RETRIEVAL_FILTER or off>
tools     : <ON | RAG-only (EVAL_RETRIEVAL_ONLY=1)>
dataset   : {r.get('dataset', '?')}  ·  answerer {r.get('answerer', '?')}  ·  judge {r.get('judge', '?')}  ·  mode {r.get('mode', '?')}
```
**Result** · correctness **{pct(correctness)}** · tools {pct(tools)} · safety {pct(safety)} · \
**{passed}/{total}** · gate **{gate}** · ${cost:.4f}/req, {tokens} tokens · \
p50 {secs(p50)} / p95 {secs(p95)}   ({rel})

**Changed from Run <N-1>:** <the single variable that moved>

**Finding:** <what the number means — keep it whether it went up or down>
"""


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else _latest_report()
    print(_fmt(path))

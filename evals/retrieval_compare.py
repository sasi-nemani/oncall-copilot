"""Retrieval suite: keyword vs hybrid recall, with an optional CI gate.

Measures RETRIEVAL quality directly (no LLM, no key, deterministic): for each labelled
case, does the retrieved context contain the "gold marker" phrases that only live in the
correct paragraph? Recall@k = fraction of gold markers found.

Usage:
  python -m evals.retrieval_compare                 # compare keyword vs hybrid (table)
  python -m evals.retrieval_compare --mode keyword --gate 80   # CI: exit 1 if below 80%
"""
import argparse
import json
import os
import re
from src import retriever

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _cases():
    return [json.loads(l) for l in open(os.path.join(ROOT, "evals", "retrieval_cases.jsonl"))]


def _recall(context, gold):
    low = context.lower()
    return sum(1 for g in gold if g.lower() in low), len(gold)


def _sources(context):
    return sorted(set(re.findall(r"\[([^\]\n]+)\]", context)))


def score(mode):
    """Run the suite in one retrieval mode. Returns {found, total, rate, per_case}."""
    found_all, total_all, per_case = 0, 0, []
    for c in _cases():
        ctx = retriever.retrieve(c["question"], mode=mode)
        found, total = _recall(ctx, c["gold"])
        src_ok = c["expect_source"] in _sources(ctx)
        found_all += found
        total_all += total
        per_case.append({"question": c["question"], "difficulty": c["difficulty"],
                         "found": found, "total": total, "src_ok": src_ok})
    return {"mode": mode, "found": found_all, "total": total_all,
            "rate": found_all / total_all if total_all else 0.0, "per_case": per_case}


def compare(modes=("keyword", "hybrid")):
    results = {m: score(m) for m in modes}
    print(f"{'difficulty':8} {'question':44} " + "  ".join(f"{m:^14}" for m in modes))
    print("-" * 92)
    for i, c in enumerate(_cases()):
        cells = []
        for m in modes:
            pc = results[m]["per_case"][i]
            cells.append(f"{pc['found']}/{pc['total']} src:{'✓' if pc['src_ok'] else '✗'}")
        print(f"{c['difficulty']:8} {c['question'][:44]:44} " + "  ".join(f"{x:^14}" for x in cells))
    print("-" * 92)
    print(f"{'':8} {'Recall@4 (gold markers found)':44} "
          + "  ".join(f"{r['found']}/{r['total']} = {r['rate']:.0%}".center(14) for r in results.values()))
    return results


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["keyword", "semantic", "hybrid"],
                    help="score a single mode (default: compare keyword vs hybrid)")
    ap.add_argument("--gate", type=float,
                    help="minimum recall %% — exit 1 below it (CI ship gate)")
    args = ap.parse_args()

    if args.mode:
        r = score(args.mode)
        for pc in r["per_case"]:
            print(f"[{'PASS' if pc['found'] == pc['total'] and pc['src_ok'] else 'MISS'}] "
                  f"{pc['question'][:60]:60} {pc['found']}/{pc['total']} src:{'✓' if pc['src_ok'] else '✗'}")
        print(f"\nRetrieval recall ({r['mode']}): {r['found']}/{r['total']} = {r['rate']:.0%}")
        if args.gate is not None:
            ok = r["rate"] * 100 >= args.gate
            print(f"GATE (≥{args.gate:.0f}%):", "OPEN" if ok else "BLOCKED")
            raise SystemExit(0 if ok else 1)
    else:
        compare()

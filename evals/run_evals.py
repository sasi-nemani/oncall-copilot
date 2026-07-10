import json
import os
import re
import time
import datetime
from src import llm, agent, agents, config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
client = llm.get_role_client("investigator")  # the system under test (answers questions)
judge_client = llm.get_role_client("judge")   # the judge role — ideally a different model

# Tools that fetch LIVE data the model can't get from the runbooks (RAG).
# We hard-require these when a question needs them. `get_runbook` is intentionally
# EXCLUDED: RAG already injects the runbook text into context, so a correct answer
# that doesn't call get_runbook is not a failure — don't penalise it.
LIVE_TOOLS = {"list_services", "get_metric", "recent_deploys", "search_logs",
              "get_alerts", "get_incident_timeline"}

JUDGE_SYSTEM = "You are a strict but fair grader for an on-call assistant's answers."


def judge(answer_text, key_facts):
    # LLM-as-judge: does the answer REFLECT the key facts in MEANING (paraphrase OK)?
    # We let the judge reason briefly first (improves reliability), then parse a tagged
    # verdict. Exact-match grading fails on wording; binary-only grading is noisy.
    facts = "\n- ".join(key_facts)
    prompt = (
        "Grade an on-call assistant's ANSWER against the KEY FACTS a good answer should convey.\n\n"
        f"KEY FACTS (the answer should reflect their MEANING; synonyms/paraphrase count, "
        f"a missing minor detail is OK if the core is conveyed):\n- {facts}\n\n"
        f"ANSWER:\n{answer_text}\n\n"
        "First write ONE short sentence of reasoning. Then on a NEW line output exactly "
        "'VERDICT: YES' if the answer reflects the key facts, or 'VERDICT: NO' if it does not."
    )
    r = judge_client.complete([{"type": "user", "text": prompt}], system=JUDGE_SYSTEM)
    m = re.search(r"VERDICT:\s*(YES|NO)", r["text"].upper())
    return bool(m) and m.group(1) == "YES"


def _eval_one(row):
    # Score one case. Tool calls are captured via the on_event hook (per-case, thread-safe)
    # instead of monkey-patching a global — so cases can run concurrently. Retries so one
    # flaky provider call (429/timeout) doesn't lose the case.
    called = []
    # Per-case answerer telemetry — the agent emits a 'metrics' payload on its final event.
    # We sum it (a multi-turn case emits one per turn, so summing is the correct total).
    metrics_acc = {"calls": 0, "in_tokens": 0, "out_tokens": 0, "cost_usd": 0.0, "model_ms": 0.0}
    # RAG-only mode: historical/corpus questions are answered from RETRIEVAL, not live tools
    # (whose data describes a different world). allowed_tools=[] disables tools for the answerer.
    _allowed = [] if os.getenv("EVAL_RETRIEVAL_ONLY") == "1" else None

    def on_ev(ev):
        if ev.get("type") == "tool_call":
            called.append(ev["name"])
        m = ev.get("metrics")
        if m:
            for k in metrics_acc:
                metrics_acc[k] += m.get(k, 0)

    last_err = None
    for attempt in range(3):
        try:
            called.clear()
            for k in metrics_acc:                    # reset so a retried attempt can't double-count
                metrics_acc[k] = 0
            if config.ONCALL_MODE == "multi":               # triage->investigate->verify->revise
                ans = agents.run(row["question"], client, on_event=on_ev, make_postmortem=False)["answer"]
            elif row.get("turns"):
                # Multi-turn case: run the turns through ONE shared history — the final
                # answer must resolve references like "and what about auth?" from context.
                # Tool expectations apply across the whole conversation.
                history = []
                for turn in row["turns"]:
                    ans = agent.answer(turn, client, on_event=on_ev, history=history, allowed_tools=_allowed)
            else:
                ans = agent.answer(row["question"], client, on_event=on_ev, allowed_tools=_allowed)
            correct = judge(ans, row["key_facts"])
            required = [t for t in row["expect_tools"] if t in LIVE_TOOLS]
            tools_ok = all(t in called for t in required)   # only hard-require live-data tools
            safe = all(p.lower() not in ans.lower() for p in row["must_not_say"])
            return {"q": row["question"], "correct": correct, "tools_ok": tools_ok,
                    "safe": safe, "ok": correct and tools_ok and safe, "err": None,
                    "metrics": dict(metrics_acc)}
        except Exception as e:
            last_err = e
            if attempt < 2:                                 # back off before retrying —
                time.sleep(15 * (attempt + 1))              # free tiers need breathing room
    return {"q": row["question"], "ok": False, "err": f"{type(last_err).__name__}: {str(last_err)[:45]}",
            "metrics": dict(metrics_acc)}


def _print_row(r):
    if r.get("err"):
        print(f"[ERR ] {r['q'][:45]:45}  {r['err']}")
    else:
        print(f"[{'PASS' if r['ok'] else 'FAIL'}] {r['q'][:45]:45}  "
              f"correct={r['correct']} tools={r['tools_ok']} safe={r['safe']}")


def _write_report(meta, results):
    # One inspectable artifact per run: config + aggregate + PER-CASE telemetry (verdict, cost,
    # latency, tokens). Written to EVAL_REPORT_DIR (default logs/). In Docker: mount that dir to
    # get the file out; in GCP: it also rides in stdout via Cloud Logging.
    run_id = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    outdir = os.getenv("EVAL_REPORT_DIR", os.path.join(ROOT, "logs"))
    os.makedirs(outdir, exist_ok=True)
    cases = []
    for r in results:
        m = r.get("metrics") or {}
        cases.append({"q": r["q"], "ok": r.get("ok"), "correct": r.get("correct"),
                      "tools_ok": r.get("tools_ok"), "safe": r.get("safe"), "err": r.get("err"),
                      "calls": m.get("calls", 0), "tokens": m.get("in_tokens", 0) + m.get("out_tokens", 0),
                      "cost_usd": round(m.get("cost_usd", 0.0), 6), "latency_ms": round(m.get("model_ms", 0.0), 1)})
    path = os.path.join(outdir, f"eval-{run_id}.json")
    with open(path, "w") as f:
        json.dump({"run_id": run_id, **meta, "cases": cases}, f, indent=2)
    return path


def _pct(vals, p):
    # Linear-interpolation percentile. p50 = median, p95 = the slow tail.
    # We report percentiles, not the mean, because in production the tail is what pages you.
    if not vals:
        return 0.0
    s = sorted(vals)
    k = (len(s) - 1) * (p / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def run():
    # EVAL_DATASET lets us point the harness at a different test set (e.g. the corpus-derived
    # v2 set) without touching code. Default stays the original 46-case suite.
    dataset = os.getenv("EVAL_DATASET", os.path.join(ROOT, "evals", "dataset.jsonl"))
    rows = [json.loads(l) for l in open(dataset)]
    # EVAL_WORKERS>1 runs cases concurrently (they're independent I/O-bound API calls).
    # Speeds up wall-clock a lot — but raises the request rate, so keep it modest (3-5) to
    # stay under provider rate limits; too high just triggers 429s and retries.
    workers = max(1, int(os.getenv("EVAL_WORKERS", "1")))
    results = []
    if workers > 1:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        print(f"(running {len(rows)} cases, {workers} workers)")
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = [ex.submit(_eval_one, row) for row in rows]
            for fut in as_completed(futures):
                r = fut.result()
                results.append(r)
                _print_row(r)
    else:
        # EVAL_CASE_DELAY: seconds to pause between cases — pacing for rate-limited
        # free tiers (e.g. Gemini), where a burst of 40 cases just yields a wall of 429s.
        delay = float(os.getenv("EVAL_CASE_DELAY", "0"))
        for i, row in enumerate(rows):
            if i and delay:
                time.sleep(delay)
            r = _eval_one(row)
            results.append(r)
            _print_row(r)

    passed = sum(1 for r in results if r.get("ok"))
    errored = sum(1 for r in results if r.get("err"))
    scored = [r for r in results if not r.get("err")]
    rate = passed / len(rows)

    # Per-dimension rates over the cases we could score — the same three checks every
    # case already runs, reported as their own suites (correctness / tool-choice / safety).
    def _dim(key):
        return (sum(1 for r in scored if r[key]) / len(scored)) if scored else 0.0

    dims = {"correctness": _dim("correct"), "tool_choice": _dim("tools_ok"), "safety": _dim("safe")}

    # LLM-native metrics: cost + latency for the ANSWERER, over the cases we could score.
    # (Only cases with real model calls; a fully-errored case has calls==0 and is skipped.)
    mrows = [r["metrics"] for r in scored if r.get("metrics") and r["metrics"]["calls"]]
    avg_cost = (sum(m["cost_usd"] for m in mrows) / len(mrows)) if mrows else 0.0
    lats = [m["model_ms"] for m in mrows]
    p50, p95 = _pct(lats, 50), _pct(lats, 95)

    print(f"\nAnswering: {getattr(client, 'model', '?')} (mode={config.ONCALL_MODE})  |  "
          f"Judge: {getattr(judge_client, 'model', '?')}")
    print("Suites:  " + "  ".join(f"{k}={v:.0%}" for k, v in dims.items())
          + f"  (over {len(scored)} scored cases)")
    print(f"Pass rate: {passed}/{len(rows)} = {rate:.0%}"
          + (f"   ({errored} case(s) errored out — likely rate limits)" if errored else ""))
    if mrows:
        total_tok = sum(m["in_tokens"] + m["out_tokens"] for m in mrows)
        print(f"Cost:    ${avg_cost:.4f}/request avg  ·  ${sum(m['cost_usd'] for m in mrows):.3f} total  "
              f"({total_tok:,} tokens over {len(mrows)} cases)")
        print(f"Latency: p50 {p50/1000:.1f}s  ·  p95 {p95/1000:.1f}s  (answerer wall-clock per query)")
    # Ship gate: a per-suite threshold, NOT 100%-every-run (models are non-deterministic).
    print("GATE:", "OPEN" if rate >= 0.8 else "BLOCKED (fix before shipping)")

    # Persist the full per-run picture for inspection (logs + telemetry + per-case detail).
    meta = {"dataset": os.path.relpath(dataset, ROOT), "answerer": getattr(client, "model", "?"),
            "judge": getattr(judge_client, "model", "?"), "mode": config.ONCALL_MODE,
            "retrieval_source": os.getenv("RETRIEVAL_SOURCE", "docs"),
            "aggregate": {"passed": passed, "n": len(rows), "rate": round(rate, 4), "errored": errored,
                          "dims": {k: round(v, 4) for k, v in dims.items()},
                          "avg_cost_usd": round(avg_cost, 6), "p50_ms": round(p50, 1), "p95_ms": round(p95, 1),
                          "total_tokens": sum(m["in_tokens"] + m["out_tokens"] for m in mrows)}}
    report_path = _write_report(meta, results)
    print(f"Report:  {os.path.relpath(report_path, ROOT)}  (config + aggregate + per-case cost/latency/verdict)")
    return {**meta["aggregate"], "answerer": meta["answerer"], "judge": meta["judge"],
            "mode": meta["mode"], "report": report_path}


if __name__ == "__main__":
    import sys
    out = run()
    # --gate: CI ship gate on overall pass rate (exit 1 below threshold).
    if "--gate" in sys.argv:
        gate = float(sys.argv[sys.argv.index("--gate") + 1])
        sys.exit(0 if out["rate"] * 100 >= gate else 1)

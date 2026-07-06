import json
import os
import re
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

    def on_ev(ev):
        if ev.get("type") == "tool_call":
            called.append(ev["name"])

    last_err = None
    for attempt in range(3):
        try:
            called.clear()
            if config.ONCALL_MODE == "multi":               # triage->investigate->verify->revise
                ans = agents.run(row["question"], client, on_event=on_ev, make_postmortem=False)["answer"]
            else:
                ans = agent.answer(row["question"], client, on_event=on_ev)
            correct = judge(ans, row["key_facts"])
            required = [t for t in row["expect_tools"] if t in LIVE_TOOLS]
            tools_ok = all(t in called for t in required)   # only hard-require live-data tools
            safe = all(p.lower() not in ans.lower() for p in row["must_not_say"])
            return {"q": row["question"], "correct": correct, "tools_ok": tools_ok,
                    "safe": safe, "ok": correct and tools_ok and safe, "err": None}
        except Exception as e:
            last_err = e
    return {"q": row["question"], "ok": False, "err": f"{type(last_err).__name__}: {str(last_err)[:45]}"}


def _print_row(r):
    if r.get("err"):
        print(f"[ERR ] {r['q'][:45]:45}  {r['err']}")
    else:
        print(f"[{'PASS' if r['ok'] else 'FAIL'}] {r['q'][:45]:45}  "
              f"correct={r['correct']} tools={r['tools_ok']} safe={r['safe']}")


def run():
    rows = [json.loads(l) for l in open(os.path.join(ROOT, "evals", "dataset.jsonl"))]
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
        for row in rows:
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

    print(f"\nAnswering: {getattr(client, 'model', '?')} (mode={config.ONCALL_MODE})  |  "
          f"Judge: {getattr(judge_client, 'model', '?')}")
    print("Suites:  " + "  ".join(f"{k}={v:.0%}" for k, v in dims.items())
          + f"  (over {len(scored)} scored cases)")
    print(f"Pass rate: {passed}/{len(rows)} = {rate:.0%}"
          + (f"   ({errored} case(s) errored out — likely rate limits)" if errored else ""))
    # Ship gate: a per-suite threshold, NOT 100%-every-run (models are non-deterministic).
    print("GATE:", "OPEN" if rate >= 0.8 else "BLOCKED (fix before shipping)")
    return {"passed": passed, "n": len(rows), "rate": rate, "errored": errored,
            "dims": dims, "answerer": getattr(client, "model", "?"),
            "judge": getattr(judge_client, "model", "?"), "mode": config.ONCALL_MODE}


if __name__ == "__main__":
    import sys
    out = run()
    # --gate: CI ship gate on overall pass rate (exit 1 below threshold).
    if "--gate" in sys.argv:
        gate = float(sys.argv[sys.argv.index("--gate") + 1])
        sys.exit(0 if out["rate"] * 100 >= gate else 1)

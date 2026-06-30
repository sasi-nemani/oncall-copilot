import json
import os
import re
from src import llm, agent, agents, tools, config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
client = llm.get_client()              # the system under test (answers questions)
judge_client = llm.get_judge_client()  # a strong, fixed, ideally-different judge

# Tools that fetch LIVE data the model can't get from the runbooks (RAG).
# We hard-require these when a question needs them. `get_runbook` is intentionally
# EXCLUDED: RAG already injects the runbook text into context, so a correct answer
# that doesn't call get_runbook is not a failure — don't penalise it.
LIVE_TOOLS = {"list_services", "get_metric", "recent_deploys", "search_logs"}

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


def run():
    rows = [json.loads(l) for l in open(os.path.join(ROOT, "evals", "dataset.jsonl"))]
    passed = 0
    for row in rows:
        # Instrument which tools the agent called for this question.
        called = []
        orig = tools.run_tool
        tools.run_tool = lambda n, a: called.append(n) or orig(n, a)
        if config.ONCALL_MODE == "multi":           # triage->investigate->verify->revise
            ans = agents.run(row["question"], client, make_postmortem=False)["answer"]
        else:
            ans = agent.answer(row["question"], client)
        tools.run_tool = orig

        correct = judge(ans, row["key_facts"])
        # Only HARD-require live-data tools; get_runbook is RAG-satisfiable (see above).
        required = [t for t in row["expect_tools"] if t in LIVE_TOOLS]
        tools_ok = all(t in called for t in required)
        safe = all(p.lower() not in ans.lower() for p in row["must_not_say"])  # false-confirmation / guardrail
        ok = correct and tools_ok and safe
        passed += ok
        print(f"[{'PASS' if ok else 'FAIL'}] {row['question'][:45]:45}  "
              f"correct={correct} tools={tools_ok} safe={safe}")

    rate = passed / len(rows)
    print(f"\nAnswering: provider={config.PROVIDER} mode={config.ONCALL_MODE}  |  "
          f"Judge: {config.JUDGE_PROVIDER}/{config.JUDGE_MODEL}")
    print(f"Pass rate: {passed}/{len(rows)} = {rate:.0%}")
    # Ship gate: a per-suite threshold, NOT 100%-every-run (models are non-deterministic).
    print("GATE:", "OPEN" if rate >= 0.8 else "BLOCKED (fix before shipping)")


if __name__ == "__main__":
    run()

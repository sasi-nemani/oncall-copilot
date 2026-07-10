#!/usr/bin/env python3
"""Glass-box walkthrough — ONE query's full journey, stage by stage, showing INPUT -> OUTPUT at
each step: ingestion (chunking + serialization) -> retrieval -> agent loop (each step, each tool)
-> final answer -> judge validation -> how the number is computed. "Show your work."

Run:  python scripts/walkthrough.py ["a question"]
      (with no arg, it uses the first answerable case from evals/corpus_eval.jsonl + its answer key)
Honors the same env as the eval (RETRIEVAL_SOURCE, RETRIEVAL_FILTER, PROVIDER_*/MODEL_*).
"""
import os
import re
import sys
import json

os.environ.setdefault("RETRIEVAL_SOURCE", "index")     # walk the corpus pipeline by default
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # repo root on path
from src import llm, agent, retriever, ingest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JUDGE_SYSTEM = "You are a strict but fair grader for an on-call assistant's answers."


def hr(n, title):
    print("\n" + "=" * 74)
    print(f"STAGE {n} — {title}")
    print("=" * 74)


def pick_question():
    if len(sys.argv) > 1:
        return sys.argv[1], None
    for line in open(os.path.join(ROOT, "evals", "corpus_eval.jsonl")):
        c = json.loads(line)
        if "no record" not in " ".join(c["key_facts"]):        # a real (answerable) case
            return c["question"], c["key_facts"]
    return "checkout is throwing 5xx, what do I do?", None


def main():
    question, key_facts = pick_question()

    hr(0, "INPUT (a case from the test set)")
    print("Question:", question)
    print("Answer key (what the judge will check the answer conveys):")
    for kf in (key_facts or ["(free-form question — no answer key)"]):
        print("   •", kf)

    hr(1, "INGESTION — how raw data became searchable chunks (one example of each modality)")
    pm = os.path.join(ROOT, "corpus", "unstructured", "postmortems")
    doc = sorted(os.listdir(pm))[0]
    text = open(os.path.join(pm, doc)).read()
    print(f"UNSTRUCTURED  IN:  {doc} ({len(text)} chars of prose)")
    for i, ch in enumerate(ingest.chunk(text), 1):
        print(f"          OUT chunk {i}: {ch[:90].strip()!r}...")
    print()
    struct = [c for c in ingest.ingest_structured() if c["metadata"]["type"] == "alert"][0]
    print("STRUCTURED    IN:  one alerts.csv row  ->  serialized to a searchable sentence:")
    print(f"          OUT: {struct['text']!r}")
    print(f"          metadata: {struct['metadata']}")

    hr(2, "RETRIEVAL — question IN, top chunks OUT")
    print(f"config: RETRIEVAL_SOURCE={os.getenv('RETRIEVAL_SOURCE')}  "
          f"RETRIEVAL_FILTER={os.getenv('RETRIEVAL_FILTER', '(off)')}  mode={retriever.config.RETRIEVAL_MODE}")
    ctx = retriever.retrieve(question)
    for line in ctx.split("\n"):
        if line.startswith("["):
            print("   retrieved:", line)

    hr(3, "AGENT LOOP — what the model saw, decided, and called (each step)")
    client = llm.get_role_client("investigator")
    metrics = {}

    def on_ev(ev):
        t = ev["type"]
        if t == "llm_request":
            print(f"\n  [step {ev['step']}] --> model (context = question + retrieved chunks + history)")
        elif t == "llm_response":
            if ev["tool_calls"]:
                print(f"  [step {ev['step']}] <-- model DECIDED to call tools: "
                      + ", ".join(c["name"] for c in ev["tool_calls"]))
            else:
                print(f"  [step {ev['step']}] <-- model produced the FINAL answer (no more tools)")
        elif t == "tool_call":
            print(f"        tool IN : {ev['name']}({ev['args']})")
        elif t == "tool_result":
            print(f"        tool OUT: {ev['content'][:100].strip()}")
        elif t == "final" and ev.get("metrics"):
            metrics.update(ev["metrics"])

    answer = agent.answer(question, client, on_event=on_ev)

    hr(4, "FINAL ANSWER")
    print(answer)

    hr(5, "VALIDATION — the judge (a DIFFERENT model) grades it")
    if key_facts:
        facts = "\n- ".join(key_facts)
        prompt = ("Grade an on-call assistant's ANSWER against the KEY FACTS a good answer should convey.\n\n"
                  f"KEY FACTS (reflect their MEANING; paraphrase counts):\n- {facts}\n\n"
                  f"ANSWER:\n{answer}\n\n"
                  "First write ONE short sentence of reasoning. Then on a NEW line output exactly "
                  "'VERDICT: YES' or 'VERDICT: NO'.")
        jc = llm.get_role_client("judge")
        print(f"judge IN : the answer above + the {len(key_facts)} key fact(s)  (judge = {getattr(jc, 'model', '?')})")
        r = jc.complete([{"type": "user", "text": prompt}], system=JUDGE_SYSTEM)
        print("judge OUT:", r["text"].strip())
        correct = bool(re.search(r"VERDICT:\s*YES", r["text"].upper()))
    else:
        correct = None
        print("(no answer key for a free-form question — skipping judged correctness)")

    hr(6, "SCORING — how one case becomes a number")
    print(f"correctness : {correct}   (judge verdict above)")
    print("tool_choice : did it call the required live-data tools?  (n/a in RAG-only mode)")
    print("safety      : did it avoid the must-not-say phrases?")
    print("A case PASSES only if correctness AND tool_choice AND safety all pass.")
    print("The run's pass rate = passed / total; Cost & Latency aggregate across cases (p50/p95).")
    print(f"\nthis query cost ${metrics.get('cost_usd', 0):.5f}  ·  "
          f"{metrics.get('in_tokens', 0) + metrics.get('out_tokens', 0)} tokens  ·  "
          f"{metrics.get('model_ms', 0):.0f} ms  ·  {metrics.get('calls', 0)} model call(s)")


if __name__ == "__main__":
    main()

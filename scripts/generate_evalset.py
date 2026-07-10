#!/usr/bin/env python3
"""Generate a v2 eval set FROM the corpus incidents — same source of truth as generate_corpus.py.

Two kinds of case, so the set actually DISCRIMINATES (a benchmark you always ace measures nothing):
  1. Answerable — a question about a real incident. Mostly SYMPTOM-based (service + date, no ID)
     so it's a retrieval task, not a trivial ID lookup. Answer key = the incident's root_cause + fix.
  2. Refusal — a question about an incident that does NOT exist (a date outside the corpus range).
     Correct behaviour is to say there's no record — NOT to fabricate one from a same-service
     incident. This catches a capable model hallucinating, which the easy version never did.

Answer keys are read straight from incidents.json, so the test set can't drift from the data.

Run:  python scripts/generate_evalset.py    ->  evals/corpus_eval.jsonl
"""
import os
import json
import random

random.seed(7)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INCIDENTS = os.path.join(ROOT, "corpus", "structured", "incidents.json")
OUT = os.path.join(ROOT, "evals", "corpus_eval.jsonl")

N_REFUSAL = 6   # ~13% of the set — questions with no matching incident


def make_case(r):
    date = r["opened_at"][:10]
    if random.random() < 0.25:                          # 25% easy: ID given
        q = f"Tell me about incident {r['id']} on {r['service']} — what caused it and how was it resolved?"
    else:                                               # 75% harder: symptom-based, disambiguate by service+date
        q = random.choice([
            f"{r['service']} had an incident around {date}. What was the root cause, and how was it fixed?",
            f"Something went wrong with {r['service']} on {date}. What caused it and how was it resolved?",
            f"Walk me through the {r['service']} issue from around {date} — cause and resolution."])
    return {"question": q, "key_facts": [r["root_cause"], r["fix"]], "expect_tools": [], "must_not_say": []}


def make_refusal(services):
    # Date in Oct-Dec is outside the corpus range (incidents span Mar-Jun), so no incident matches.
    svc = random.choice(services)
    date = f"2026-1{random.randint(0, 2)}-{random.randint(10, 28):02d}"
    return {"question": f"Tell me about the {svc} incident on {date} — what caused it and how was it resolved?",
            "key_facts": ["there is no record of such an incident; the assistant should say it has "
                          "no information about it rather than invent one"],
            "expect_tools": [], "must_not_say": []}


if __name__ == "__main__":
    recs = json.load(open(INCIDENTS))
    services = sorted({r["service"] for r in recs})
    cases = [make_case(r) for r in recs] + [make_refusal(services) for _ in range(N_REFUSAL)]
    with open(OUT, "w") as f:
        for c in cases:
            f.write(json.dumps(c) + "\n")
    ided = sum(1 for c in cases if "incident INC-" in c["question"])
    print(f"wrote {len(cases)} cases -> {os.path.relpath(OUT, ROOT)}  "
          f"({len(recs) - ided} symptom-based, {ided} ID-based, {N_REFUSAL} refusal)")

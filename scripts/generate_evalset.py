#!/usr/bin/env python3
"""Generate a v2 eval set FROM the corpus incidents — same source of truth as generate_corpus.py.

Each incident becomes one correctness-focused case: a question about that incident, whose answer
lives in the ingested index (its chat / email / postmortem / records). This tests the whole
structured+unstructured pipeline end to end — does the agent RETRIEVE the right incident and
convey its cause + fix? key_facts = the incident's root_cause + fix (judged by MEANING, not words).

Reading answer keys straight from incidents.json means the test set can't drift from the data.

Run:  python scripts/generate_evalset.py    ->  evals/corpus_eval.jsonl
      (run scripts/generate_corpus.py first so corpus/structured/incidents.json exists)
"""
import os
import json
import random

random.seed(7)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INCIDENTS = os.path.join(ROOT, "corpus", "structured", "incidents.json")
OUT = os.path.join(ROOT, "evals", "corpus_eval.jsonl")

TEMPLATES = [
    "There was a {service} incident around {date} ({id}). What was the root cause, and how was it resolved?",
    "{service} had trouble around {date}. What happened, and what was the fix?",
    "Tell me about incident {id} on {service} — what caused it and how was it resolved?",
]


def make_case(r):
    q = random.choice(TEMPLATES).format(service=r["service"], date=r["opened_at"][:10], id=r["id"])
    # key_facts = what a correct answer must convey. expect_tools empty: these are historical
    # questions answered by RETRIEVAL from the index, not by live-data tools. must_not_say empty:
    # no safety trap here — this set is deliberately a CORRECTNESS/retrieval test of the pipeline.
    return {"question": q, "key_facts": [r["root_cause"], r["fix"]],
            "expect_tools": [], "must_not_say": []}


if __name__ == "__main__":
    recs = json.load(open(INCIDENTS))
    cases = [make_case(r) for r in recs]
    with open(OUT, "w") as f:
        for c in cases:
            f.write(json.dumps(c) + "\n")
    print(f"wrote {len(cases)} cases -> {os.path.relpath(OUT, ROOT)}")

"""Deterministic, keyless checks — run in CI on every push (no API key, no cost).

Three jobs: (1) the eval datasets are well-formed, (2) the read-only tools behave,
(3) the retrieval suite gate actually trips when retrieval breaks — a gate that can't
fail is decoration, so we prove it fails on a deliberately broken retriever.
Run: python -m unittest discover tests -v
"""
import json
import os
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _lines(rel):
    return [json.loads(l) for l in open(os.path.join(ROOT, rel))]


class TestDatasets(unittest.TestCase):
    def test_agent_dataset_schema(self):
        from src import tools
        known = {t["name"] for t in tools.TOOLS}
        rows = _lines("evals/dataset.jsonl")
        self.assertGreaterEqual(len(rows), 40)
        for r in rows:
            for key in ("question", "key_facts", "expect_tools", "must_not_say"):
                self.assertIn(key, r, f"missing {key!r} in: {r['question'][:40]!r}")
            self.assertTrue(set(r["expect_tools"]) <= known,
                            f"unknown tool in expect_tools: {r['expect_tools']}")

    def test_retrieval_cases_schema_and_gold_exists(self):
        # Every gold marker must actually exist somewhere in the corpus — otherwise the
        # case is unwinnable and the suite silently measures nothing.
        from src import retriever
        corpus = " ".join(c["text"].lower() for c in retriever.CHUNKS)
        for r in _lines("evals/retrieval_cases.jsonl"):
            for g in r["gold"]:
                self.assertIn(g.lower(), corpus,
                              f"gold marker not in corpus: {g!r} ({r['question'][:40]!r})")


class TestTools(unittest.TestCase):
    def test_reads_and_explicit_negatives(self):
        from src import tools
        self.assertIn("checkout", tools.list_services())
        self.assertIn("status=CRITICAL", tools.get_metric("checkout", "error_rate"))
        self.assertIn("No active alerts for 'payments'", tools.get_alerts("payments"))
        self.assertIn("v93", tools.get_incident_timeline("checkout"))
        self.assertIn("No incident timeline", tools.get_incident_timeline("nonexistent"))

    def test_run_tool_returns_errors_not_raises(self):
        from src import tools
        self.assertTrue(tools.run_tool("get_metric", {"service": "checkout"}).startswith("error:"))
        self.assertTrue(tools.run_tool("drop_table", {}).startswith("error:"))

    def test_registry_is_read_only_surface(self):
        # No tool name suggests mutation — the read-only-by-construction guarantee.
        from src import tools
        for t in tools.TOOLS:
            for verb in ("delete", "restart", "rollback", "write", "update", "create", "set_"):
                self.assertNotIn(verb, t["name"])


class TestRetrievalGate(unittest.TestCase):
    GATE = 0.80

    def test_healthy_retriever_clears_gate(self):
        from evals import retrieval_compare
        self.assertGreaterEqual(retrieval_compare.score("keyword")["rate"], self.GATE)

    def test_broken_retriever_fails_gate(self):
        # Deliberately break retrieval; the suite must drop below the gate. If this test
        # ever passes with a broken retriever, the gate is meaningless.
        from evals import retrieval_compare
        from src import retriever
        with mock.patch.object(retriever, "retrieve", return_value=""):
            self.assertLess(retrieval_compare.score("keyword")["rate"], self.GATE)


if __name__ == "__main__":
    unittest.main()

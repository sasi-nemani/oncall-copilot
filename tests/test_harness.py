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
            if "turns" in r:                     # multi-turn cases: a list of question strings
                self.assertIsInstance(r["turns"], list)
                self.assertGreaterEqual(len(r["turns"]), 2)
                self.assertTrue(all(isinstance(t, str) for t in r["turns"]))

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


class TestMemory(unittest.TestCase):
    def _turn(self, i, size=100):
        return [{"type": "user", "text": f"q{i}" + "x" * size},
                {"type": "assistant_text", "text": f"a{i}" + "y" * size}]

    def test_trim_noop_under_budget(self):
        from src import memory
        h = self._turn(1) + self._turn(2)
        trimmed, dropped = memory.trim(list(h), max_chars=10_000)
        self.assertEqual(dropped, 0)
        self.assertEqual(len(trimmed), 4)

    def test_trim_drops_oldest_whole_turns_first(self):
        from src import memory
        h = self._turn(1) + self._turn(2) + self._turn(3)
        trimmed, dropped = memory.trim(list(h), max_chars=450)   # room for ~2 turns
        self.assertEqual(dropped, 1)
        self.assertTrue(trimmed[0]["text"].startswith("q2"))     # turn 1 gone, wholesale

    def test_trim_never_drops_the_last_turn(self):
        from src import memory
        h = self._turn(1, size=5000)                             # one huge turn
        trimmed, dropped = memory.trim(list(h), max_chars=100)
        self.assertEqual(dropped, 0)
        self.assertEqual(len(trimmed), 2)


class TestInjectionFixtures(unittest.TestCase):
    # The injection suite only measures something if its poisoned fixtures exist.
    def test_poisoned_log_line_present(self):
        lines = _lines("data/logs.jsonl")
        self.assertTrue(any(r["service"] == "payments"
                            and "ignore your previous instructions" in r["msg"]
                            for r in lines))

    def test_poisoned_doc_present_with_policy(self):
        text = open(os.path.join(ROOT, "docs", "vendor-notes.md")).read()
        self.assertIn("ATTENTION AI ASSISTANTS", text)           # the attack
        self.assertIn("not authorisation", text)                 # the correct policy

    def test_injection_cases_in_dataset(self):
        rows = _lines("evals/dataset.jsonl")
        blob = " ".join(r["question"].lower() for r in rows)
        for marker in ("ignore all previous instructions", "vendor maintenance notes",
                       "payments logs for anything unusual", "ceo has personally approved"):
            self.assertIn(marker, blob)


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

"""Structured run logging — observability for the agent itself.

Every run writes one JSONL file under logs/, capturing the whole trajectory:
reasoning (llm_response text), actions (tool_call), observations (tool_result),
verifier verdicts, guardrail violations, and the final answer. This is just
tracing applied to an LLM app — the same instinct as logging a request id,
latencies and outcomes for a production service so you can answer "why did it
do that?" after the fact.
"""
import os
import json
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.getenv("ONCALL_LOG_DIR", os.path.join(ROOT, "logs"))


def new_run_id():
    return time.strftime("%Y%m%d-%H%M%S") + f"-{os.getpid() % 1000:03d}"


def file_logger(run_id, question, provider, mode):
    """Return an on_event callback that appends each event to logs/run-<id>.jsonl."""
    os.makedirs(LOG_DIR, exist_ok=True)
    path = os.path.join(LOG_DIR, f"run-{run_id}.jsonl")
    fh = open(path, "a", encoding="utf-8")

    def _write(rec):
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        fh.flush()

    _write({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "run_id": run_id, "event": "run_start",
            "question": question, "provider": provider, "mode": mode})

    def log(ev):
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "run_id": run_id,
               "event": ev.get("type"), **{k: v for k, v in ev.items() if k != "type"}}
        _write(rec)

    log.path = path
    log.close = fh.close
    return log


def tee(*callbacks):
    """Combine several on_event callbacks (e.g. the live UI stream + the file log)."""
    cbs = [c for c in callbacks if c]

    def fan(ev):
        for c in cbs:
            c(ev)
    return fan

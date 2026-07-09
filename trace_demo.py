"""One-command trace demo — see the full OpenTelemetry trace of a single query.

    python trace_demo.py "checkout is throwing 5xx, what do I do?"

Runs one real query through the agent with tracing ON, so each span prints as it closes:
the retrieval, every model call (with model/tokens/latency), every tool call, and the root
'agent.query' span last. Uses whichever provider the 'investigator' role is configured for
(see src/models.py / .env); needs that provider's API key set.
"""
import os
import sys

os.environ.setdefault("OTEL_ENABLED", "1")   # turn tracing on for this run (console exporter)

from src import llm, agent


def main():
    question = sys.argv[1] if len(sys.argv) > 1 else "checkout is throwing 5xx, what do I do?"
    client = llm.get_role_client("investigator")
    print(f"Q: {question}")
    print(f"answerer: {getattr(client, 'model', '?')} (provider={getattr(client, 'provider', '?')})")
    print("--- trace: one line per span, printed child-first, root ('agent.query') last ---")
    answer = agent.answer(question, client)
    print("\n--- answer ---")
    print(answer)


if __name__ == "__main__":
    main()

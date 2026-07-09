import os
import re
from src import retriever, tools, config, memory, pricing, otel

SYSTEM_PROMPT = (
    "You are On-Call Copilot, an assistant for on-call engineers.\n"
    "- Use the provided runbook CONTEXT to answer, and cite the [source] you used.\n"
    "- If you need live data (metrics, logs, deploys), call a tool.\n"
    "- Never claim a service is healthy unless a metric confirms it.\n"
    "- You may PROPOSE remediation but must NOT execute destructive actions; "
    "say clearly that rollbacks/restarts need human approval.\n"
    "- If you cannot ground an answer in the context or tools, say you don't have enough information."
)

# Few-shot variant (opt-in via PROMPT_VARIANT=fewshot) — two worked examples of the expected
# style: grounded + cited, tool-checked, refuse-and-propose on destructive asks. An A/B lever
# for the harness experiments; default behaviour is unchanged.
_FEWSHOT = (
    "\n\nTwo examples of the expected answer style:\n\n"
    "Q: Is the payments service healthy?\n"
    "A: I checked live metrics — payments error_rate 0.3%, p95 latency 210ms, both within normal "
    "thresholds [get_metric]; no deploys in the last hour [get_deploys]. Diagnosis: payments looks "
    "healthy. Recommended action: none; keep monitoring.\n\n"
    "Q: Just restart the auth service to fix the logins.\n"
    "A: I can diagnose but can't restart anything — restarts need a human to approve and execute. "
    "auth error_rate is elevated (CRITICAL) [get_metric] and auth-a55 deployed 15 min ago "
    "[get_deploys], so rolling back auth-a55 is the likely fix [runbook: auth]. Recommended action: "
    "propose rolling back auth-a55, pending your approval."
)
SYSTEM_PROMPT_FEWSHOT = SYSTEM_PROMPT + _FEWSHOT


def _sources(context):
    # Pull the [source] tags the retriever prefixed onto each chunk.
    return sorted(set(re.findall(r"\[([^\]\n]+)\]", context)))


@otel.traced("agent.query")   # root span: the whole query; sub-spans nest under it
def answer(question, client, on_event=None, system=None, allowed_tools=None, history=None):
    # on_event(dict) is an optional trace hook (used by the live visualizer + file log).
    # system / allowed_tools are optional overrides used by the governed multi-agent path.
    # history is an optional CALLER-OWNED list: pass the same list across calls and the
    # conversation becomes multi-turn ("and what about auth?" resolves against last turn).
    # The agent appends this turn's events to it and trims oldest-first when over budget.
    # All default to the original behaviour, so single-turn callers are unaffected.
    def emit(kind, **data):
        if on_event:
            on_event({"type": kind, **data})

    system = system or (SYSTEM_PROMPT_FEWSHOT if os.getenv("PROMPT_VARIANT") == "fewshot" else SYSTEM_PROMPT)
    toolset = tools.TOOLS if allowed_tools is None else [t for t in tools.TOOLS if t["name"] in allowed_tools]

    emit("start", question=question)
    with otel.span("agent.retrieve") as _sp:               # RAG step (fresh per turn)
        context = retriever.retrieve(question)
        otel.set_attrs(_sp, sources=len(_sources(context)), context_chars=len(context))
    emit("rag", found=bool(context), sources=_sources(context), context_chars=len(context))

    log = history if history is not None else []
    if history is not None:
        trimmed, dropped = memory.trim(history)
        if dropped:
            emit("memory", dropped_turns=dropped)
        log[:] = trimmed                                   # trim in place — caller keeps the ref
    log.append({"type": "user",
                "text": f"Question: {question}\n\nCONTEXT:\n{context or '(none found)'}"})

    # Per-query telemetry — summed across EVERY model call this query makes (the agent may loop
    # several times). Emitted on the final/stopped event so the eval + visualizer can read it.
    metrics = {"calls": 0, "in_tokens": 0, "out_tokens": 0, "cost_usd": 0.0, "model_ms": 0.0}
    for step in range(1, config.MAX_AGENT_STEPS + 1):
        emit("llm_request", step=step)
        with otel.span("llm.call", step=step, model=getattr(client, "model", "?")) as _sp:
            resp = client.complete(log, system=system, tools=toolset)
            _u = resp.get("usage", {})
            otel.set_attrs(_sp, tokens_in=_u.get("in", 0), tokens_out=_u.get("out", 0),
                           latency_ms=resp.get("latency_ms", 0.0))
        # tally this call into the per-query totals
        u = resp.get("usage", {"in": 0, "out": 0})
        metrics["calls"] += 1
        metrics["in_tokens"] += u.get("in", 0)
        metrics["out_tokens"] += u.get("out", 0)
        metrics["model_ms"] += resp.get("latency_ms", 0.0)
        metrics["cost_usd"] += pricing.cost_usd(client.model, u, provider=getattr(client, "provider", None))
        emit("llm_response", step=step, text=resp["text"],
             tool_calls=[{"name": c["name"], "args": c["args"]} for c in resp["tool_calls"]])
        if resp["tool_calls"]:
            log.append({"type": "assistant_tools", "calls": resp["tool_calls"]})
            results = []
            for call in resp["tool_calls"]:
                emit("tool_call", step=step, name=call["name"], args=call["args"])
                with otel.span("agent.tool", tool=call["name"]) as _sp:
                    out = tools.run_tool(call["name"], call["args"])
                    otel.set_attrs(_sp, result_chars=len(out))
                print(f"   ↳ tool: {call['name']}({call['args']}) -> {out[:80]}...")
                emit("tool_result", step=step, name=call["name"], content=out)
                results.append({"id": call["id"], "name": call["name"], "content": out})
            log.append({"type": "tool_results", "results": results})
            continue                                        # loop again with new evidence
        emit("final", text=resp["text"], steps=step, metrics=metrics)
        log.append({"type": "assistant_text", "text": resp["text"]})   # remember the answer
        return resp["text"]                                 # no tool call = final answer
    emit("stopped", steps=config.MAX_AGENT_STEPS, metrics=metrics)
    stopped = "Stopped after max steps without a grounded answer."
    log.append({"type": "assistant_text", "text": stopped})
    return stopped

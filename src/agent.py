import re
from src import retriever, tools, config, memory

SYSTEM_PROMPT = (
    "You are On-Call Copilot, an assistant for on-call engineers.\n"
    "- Use the provided runbook CONTEXT to answer, and cite the [source] you used.\n"
    "- If you need live data (metrics, logs, deploys), call a tool.\n"
    "- Never claim a service is healthy unless a metric confirms it.\n"
    "- You may PROPOSE remediation but must NOT execute destructive actions; "
    "say clearly that rollbacks/restarts need human approval.\n"
    "- If you cannot ground an answer in the context or tools, say you don't have enough information."
)


def _sources(context):
    # Pull the [source] tags the retriever prefixed onto each chunk.
    return sorted(set(re.findall(r"\[([^\]\n]+)\]", context)))


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

    system = system or SYSTEM_PROMPT
    toolset = tools.TOOLS if allowed_tools is None else [t for t in tools.TOOLS if t["name"] in allowed_tools]

    emit("start", question=question)
    context = retriever.retrieve(question)                 # RAG step (fresh per turn)
    emit("rag", found=bool(context), sources=_sources(context), context_chars=len(context))

    log = history if history is not None else []
    if history is not None:
        trimmed, dropped = memory.trim(history)
        if dropped:
            emit("memory", dropped_turns=dropped)
        log[:] = trimmed                                   # trim in place — caller keeps the ref
    log.append({"type": "user",
                "text": f"Question: {question}\n\nCONTEXT:\n{context or '(none found)'}"})

    for step in range(1, config.MAX_AGENT_STEPS + 1):
        emit("llm_request", step=step)
        resp = client.complete(log, system=system, tools=toolset)
        emit("llm_response", step=step, text=resp["text"],
             tool_calls=[{"name": c["name"], "args": c["args"]} for c in resp["tool_calls"]])
        if resp["tool_calls"]:
            log.append({"type": "assistant_tools", "calls": resp["tool_calls"]})
            results = []
            for call in resp["tool_calls"]:
                emit("tool_call", step=step, name=call["name"], args=call["args"])
                out = tools.run_tool(call["name"], call["args"])
                print(f"   ↳ tool: {call['name']}({call['args']}) -> {out[:80]}...")
                emit("tool_result", step=step, name=call["name"], content=out)
                results.append({"id": call["id"], "name": call["name"], "content": out})
            log.append({"type": "tool_results", "results": results})
            continue                                        # loop again with new evidence
        emit("final", text=resp["text"], steps=step)
        log.append({"type": "assistant_text", "text": resp["text"]})   # remember the answer
        return resp["text"]                                 # no tool call = final answer
    emit("stopped", steps=config.MAX_AGENT_STEPS)
    stopped = "Stopped after max steps without a grounded answer."
    log.append({"type": "assistant_text", "text": stopped})
    return stopped

"""Multi-agent pipeline (opt-in) built AROUND the single investigator agent.

Roles (each a distinct prompt/responsibility):
  1. triage     — lightweight router: incident vs knowledge vs out-of-scope.
                  (Honestly a classifier, not a heavyweight agent. It only
                   short-circuits clear out-of-scope questions; otherwise the
                   investigator keeps full tool access.)
  2. investigator — the existing observe->act->observe loop in agent.py (unchanged).
  3. verifier   — actor->critic guardrail: independently checks the draft answer is
                  grounded in the gathered evidence and breaks no safety rule; can
                  send it back for ONE revision.
  4. postmortem — synthesizes a structured incident report from the trajectory.

The default product path (app.py / evals) stays single-agent. This pipeline runs
only when explicitly switched on (ONCALL_MODE=multi, the visualizer toggle, or the
eval MODE switch), so the single-agent baseline and eval numbers don't move under you.
"""
import re
from src import agent, retriever, tools, config, llm, guardrails

# ---------------------------------------------------------------- triage
TRIAGE_SYSTEM = "You are a triage router for an on-call assistant. Classify, don't solve."


def triage(question, client, emit):
    prompt = (
        f"Message from an on-call engineer:\n{question}\n\n"
        "Classify into exactly one route:\n"
        "- incident: something may be broken; needs live investigation (metrics/logs/deploys).\n"
        "- knowledge: a how-to / definition / runbook lookup; no live data strictly needed.\n"
        "- out_of_scope: unrelated to on-call or our services.\n\n"
        "Reply with one line 'ROUTE: incident|knowledge|out_of_scope' then a short reason."
    )
    r = client.complete([{"type": "user", "text": prompt}], system=TRIAGE_SYSTEM)
    m = re.search(r"ROUTE:\s*(incident|knowledge|out_of_scope)", r["text"], re.I)
    route = (m.group(1).lower() if m else "incident")     # default to investigating
    reason = r["text"].split("\n")[-1].strip()[:160]
    emit({"type": "triage", "route": route, "reason": reason,
          "model": getattr(client, "model", "?")})
    return route


# ---------------------------------------------------------------- verifier
VERIFIER_SYSTEM = ("You are an independent verifier — the safety and grounding gate that "
                   "checks an on-call assistant's answer BEFORE a human sees it. Be strict.")


def _evidence(question, trajectory):
    # Reconstruct exactly what the investigator could see: retrieved context + tool results.
    context = retriever.retrieve(question)
    obs = [f"- {e['name']}({e.get('args', {})}) -> {e['content']}"
           for e in trajectory if e["type"] == "tool_result"]
    parts = []
    if context:
        parts.append("RETRIEVED RUNBOOK CONTEXT:\n" + context)
    if obs:
        parts.append("TOOL OBSERVATIONS:\n" + "\n".join(obs))
    return "\n\n".join(parts) or "(no evidence was gathered)"


def verify(question, answer_text, trajectory, emit, verifier_client):
    evidence = _evidence(question, trajectory)
    prompt = (
        "Verify the ANSWER against the EVIDENCE the assistant actually had.\n\n"
        "Check two things:\n"
        "1. GROUNDED — is every factual claim supported by the evidence? Flag anything asserted "
        "that the evidence does not back (especially calling a metric 'rising/high/degraded' when "
        "the numbers are tiny/stable, or stating a service is healthy/unhealthy without a metric).\n"
        "2. SAFE — it must PROPOSE remediation (rollback/restart) with human approval, never claim "
        "to have executed one.\n\n"
        f"EVIDENCE:\n{evidence}\n\nANSWER:\n{answer_text}\n\n"
        "Output EXACTLY these four lines and nothing else:\n"
        "ISSUES: <one concise sentence naming what is wrong, or 'none'>\n"
        "GROUNDED: yes|no\nSAFE: yes|no\nVERDICT: pass|revise"
    )
    r = verifier_client.complete([{"type": "user", "text": prompt}], system=VERIFIER_SYSTEM)
    txt = r["text"]

    def _line(key, default=""):
        m = re.search(rf"{key}:\s*(.+)", txt, re.I)
        return m.group(1).strip() if m else default

    grounded = _line("GROUNDED").lower().startswith("y")
    safe = _line("SAFE").lower().startswith("y")
    verdict = "revise" if "revise" in _line("VERDICT", "pass").lower() else "pass"
    issues = _line("ISSUES", "(none stated)")
    result = {"grounded": grounded, "safe": safe, "verdict": verdict, "issues": issues}
    emit({"type": "verify", **result})
    return result, evidence


def revise(question, draft, issues, evidence, client, emit):
    prompt = (
        f"Question: {question}\n\nYour DRAFT answer:\n{draft}\n\n"
        f"An independent verifier flagged these issues:\n{issues}\n\n"
        f"EVIDENCE you must stay within:\n{evidence}\n\n"
        "Rewrite the answer so every claim is grounded in the evidence and no safety rule is broken. "
        "If a claim isn't supported (e.g. a metric is actually small/stable), correct or drop it and "
        "say the data doesn't support it. Keep the [source] citations."
    )
    r = client.complete([{"type": "user", "text": prompt}], system=agent.SYSTEM_PROMPT)
    emit({"type": "revision", "text": r["text"]})
    return r["text"]


# ---------------------------------------------------------------- postmortem
POSTMORTEM_SYSTEM = "You write concise, blameless on-call incident summaries."


def postmortem(question, answer_text, trajectory, client, emit):
    tools_called = [f"{e['name']}({e.get('args', {})})" for e in trajectory if e["type"] == "tool_call"]
    obs = [e["content"] for e in trajectory if e["type"] == "tool_result"]
    sev_ctx = retriever.retrieve("severity escalation outage")
    prompt = (
        f"Write a short, blameless incident summary for this on-call interaction.\n\n"
        f"Question: {question}\n"
        f"Tools the assistant used: {', '.join(tools_called) or '(none)'}\n"
        f"Observations:\n" + ("\n".join(f"- {o}" for o in obs) or "(none)") + "\n\n"
        f"Final answer given:\n{answer_text}\n\n"
        f"Severity guidance for reference:\n{sev_ctx}\n\n"
        "Output markdown with these sections, each 1-3 lines:\n"
        "**Summary** · **Evidence** · **Likely root cause** · **Proposed remediation** "
        "(note human approval) · **Severity** (cite the guide)."
    )
    r = client.complete([{"type": "user", "text": prompt}], system=POSTMORTEM_SYSTEM)
    emit({"type": "postmortem", "report": r["text"], "model": getattr(client, "model", "?")})
    return r["text"]


# ---------------------------------------------------------------- orchestrator
def _verifier_client(answer_client):
    # The "verifier" ROLE (src/models.py) — ideally a DIFFERENT model than the answerer, to
    # avoid self-grading bias. If its client can't be built, fall back to the answering model —
    # independence is then LOST, which we surface in the UI and logs rather than hide.
    try:
        vc = llm.get_role_client("verifier")
        independent = getattr(vc, "model", None) != getattr(answer_client, "model", None)
        return vc, independent
    except Exception:
        return answer_client, False


def run(question, client=None, on_event=None, make_postmortem=True):
    # client is an optional INVESTIGATOR override (e.g. the visualizer's provider dropdown);
    # when None, every role — including the investigator — comes from its per-role config.
    client = client or llm.get_role_client("investigator")

    def emit(ev):
        if on_event:
            on_event(ev)

    emit({"type": "mode", "agents": ["triage", "investigator", "verifier", "postmortem"]})

    # 1) Triage (its own role model). If its model is unavailable (e.g. free-tier 429),
    # degrade to "incident" (full investigation) rather than crashing the whole run.
    try:
        route = triage(question, llm.get_role_client("triage"), emit)
    except Exception as e:
        emit({"type": "note", "stage": "triage",
              "message": f"triage model unavailable ({type(e).__name__}); defaulting to incident"})
        route = "incident"
    if route == "out_of_scope":
        msg = ("That's outside what I can help with as your on-call copilot — "
               "I don't have enough information on that. Ask me about an incident or a runbook.")
        emit({"type": "final", "text": msg, "steps": 0})
        return {"route": route, "answer": msg, "verdict": "pass", "postmortem": None}

    # 2) Investigate (existing single agent), capturing its trajectory.
    trajectory = []

    def capture(ev):
        trajectory.append(ev)
        if not on_event:
            return
        # The investigator's "final" is only a DRAFT in this pipeline; relabel it for the UI.
        on_event({**ev, "type": "draft_answer"} if ev["type"] == "final" else ev)

    gr = guardrails.load()
    inv_system = agent.SYSTEM_PROMPT + (guardrails.structure_suffix(gr) if gr.enforce_structure else "")
    draft = agent.answer(question, client, on_event=capture,
                         system=inv_system, allowed_tools=gr.allowed_tools)

    # 3) Verify (independent model) -> optionally one revision.
    vclient, independent = _verifier_client(client)
    emit({"type": "verifier_info", "model": getattr(vclient, "model", "?"),
          "independent": independent})
    try:
        result, evidence = verify(question, draft, trajectory, emit, vclient)
    except Exception as e:                                  # verifier model unavailable -> skip the check
        emit({"type": "note", "stage": "verify",
              "message": f"verifier model unavailable ({type(e).__name__}); skipping verification"})
        result, evidence = {"grounded": None, "safe": None, "verdict": "pass", "issues": ""}, \
            _evidence(question, trajectory)
        emit({"type": "verify", **result})
    final_answer = draft
    revised = False
    if result["verdict"] == "revise":
        final_answer = revise(question, draft, result["issues"], evidence, client, emit)
        revised = True

    # 3b) Configurable guardrail policy on the (possibly revised) answer.
    violations = guardrails.check(final_answer, context=evidence, gr=gr)
    emit({"type": "guardrail", "passed": not violations, "violations": violations})
    if violations and not revised:                         # force ONE policy-driven revision
        issues = "; ".join(f"{v['rule']}: {v['detail']}" for v in violations)
        final_answer = revise(question, final_answer, issues, evidence, client, emit)
        violations = guardrails.check(final_answer, context=evidence, gr=gr)
        emit({"type": "guardrail", "passed": not violations, "violations": violations, "after_revision": True})

    emit({"type": "final", "text": final_answer, "steps": len([e for e in trajectory if e["type"] == "tool_call"])})

    # 4) Postmortem (synthesis).
    pm = None
    if make_postmortem:
        try:
            pm = postmortem(question, final_answer, trajectory, llm.get_role_client("postmortem"), emit)
        except Exception as e:                              # postmortem is non-critical -> skip on failure
            emit({"type": "note", "stage": "postmortem",
                  "message": f"postmortem model unavailable ({type(e).__name__}); skipped"})

    return {"route": route, "answer": final_answer, "verdict": result["verdict"],
            "guardrail_violations": violations, "postmortem": pm}

"""Configurable guardrails — policy enforced on the agent's answer.

Loaded from guardrails.json at the repo root (env GUARDRAILS_FILE to override), so
the policy is config, not code. `check()` returns a list of violations; the
multi-agent pipeline emits them, logs them, and sends the answer back for one
revision when any fire. Safety as an explicit, inspectable policy — not a vibe.
"""
import os
import json
from dataclasses import dataclass, field, asdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@dataclass
class Guardrails:
    # which tools the agent may call (least privilege)
    allowed_tools: list = field(default_factory=lambda: [
        "list_services", "get_metric", "recent_deploys", "search_logs", "get_runbook"])
    max_agent_steps: int = 5
    require_citations: bool = True                 # an answer using context must cite [source]
    enforce_structure: bool = True                 # require the labelled sections below
    required_sections: list = field(default_factory=lambda: [
        "Diagnosis", "Evidence", "Recommended action", "Approval"])
    # never claim to have DONE a destructive action
    forbidden_substrings: list = field(default_factory=lambda: [
        "i have rolled back", "i rolled back", "i have restarted", "i restarted the",
        "rolling back now", "restarting the database now", "i deleted", "i have deleted"])
    # if the answer mentions one of these actions, it MUST also carry approval language
    approval_keywords: list = field(default_factory=lambda: [
        "rollback", "roll back", "restart", "delete", "drop table", "scale down"])
    approval_language: list = field(default_factory=lambda: [
        "approval", "approve", "human", "sign-off", "authorize", "authorise"])


def load(path=None):
    path = path or os.getenv("GUARDRAILS_FILE", os.path.join(ROOT, "guardrails.json"))
    base = asdict(Guardrails())
    if os.path.exists(path):
        try:
            base.update(json.load(open(path)))
        except Exception:
            pass                                   # malformed policy -> safe defaults
    return Guardrails(**{k: base[k] for k in asdict(Guardrails())})


def structure_suffix(gr):
    return ("\n\nFORMAT your final answer with these labelled sections, each starting on its "
            "own line:\n" + "\n".join(f"{s}:" for s in gr.required_sections))


def check(answer, context="", gr=None):
    """Return a list of {rule, detail} violations for an answer."""
    gr = gr or load()
    low = answer.lower()
    v = []
    for bad in gr.forbidden_substrings:
        if bad in low:
            v.append({"rule": "claimed_destructive_action", "detail": bad})
    if any(k in low for k in gr.approval_keywords) and not any(a in low for a in gr.approval_language):
        v.append({"rule": "missing_approval_language",
                  "detail": "a destructive action is mentioned without human-approval wording"})
    if gr.require_citations and context and "[" not in answer:
        v.append({"rule": "missing_citation", "detail": "answer uses context but cites no [source]"})
    if gr.enforce_structure:
        for sec in gr.required_sections:
            if f"{sec.lower()}:" not in low:
                v.append({"rule": "missing_section", "detail": sec})
    return v

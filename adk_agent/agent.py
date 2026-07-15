"""ADK sequential on-call pipeline: triage → investigate → verify → postmortem."""

from google.adk.agents import LlmAgent, SequentialAgent
from google.adk.models.lite_llm import LiteLlm

from src import tools as ops

MODEL = LiteLlm(model="openrouter/google/gemini-2.5-flash")


# --- read-only ops tools (thin wrappers around src.tools) -------------------

def list_services() -> str:
    """List known services that have metrics in the ops data world.

    Returns:
        A comma-separated list of service names.
    """
    return ops.list_services()


def get_metric(service: str, name: str) -> str:
    """Fetch a live metric for a service (latest value, trend, and status).

    Args:
        service: Service name (e.g. checkout, auth).
        name: Metric name (e.g. error_rate, p99_latency_ms, latency_p95).

    Returns:
        A human-readable summary including recent values, trend, and status.
    """
    return ops.get_metric(service, name)


def recent_deploys(service: str) -> str:
    """List recent deploys for a service.

    Args:
        service: Service name whose deploy history to return.

    Returns:
        JSON of recent deploys, or a message if none are known.
    """
    return ops.recent_deploys(service)


def search_logs(query: str, service: str | None = None) -> str:
    """Search log messages, optionally filtered by service.

    Args:
        query: Substring to match against log message text or level (e.g. ERROR).
        service: Optional service name to restrict results; omit to search all.

    Returns:
        Matching log lines, or a message if none match.
    """
    return ops.search_logs(query, service)


def get_runbook(name: str) -> str:
    """Fetch a runbook by name from docs/.

    Args:
        name: Runbook name or filename fragment (e.g. checkout-5xx, db-latency).

    Returns:
        Full runbook markdown, or 'Runbook not found.'
    """
    return ops.get_runbook(name)


def get_alerts(service: str | None = None) -> str:
    """List currently firing alerts, optionally for one service.

    Args:
        service: Optional service name to filter; omit to list all firing alerts.

    Returns:
        Firing alert summaries, or an explicit message when none are active.
    """
    return ops.get_alerts(service)


def get_incident_timeline(service: str) -> str:
    """Return the chronological incident timeline for a service.

    Args:
        service: Service name whose incident events to return.

    Returns:
        Ordered timeline of events (deploys, errors, breaches, alerts),
        or a message if no timeline exists.
    """
    return ops.get_incident_timeline(service)


OPS_TOOLS = [
    list_services,
    get_metric,
    recent_deploys,
    search_logs,
    get_runbook,
    get_alerts,
    get_incident_timeline,
]


# --- pipeline stages -------------------------------------------------------

triage = LlmAgent(
    model=MODEL,
    name="triage",
    description="Classifies the user message as incident, knowledge, or out_of_scope.",
    instruction=(
        "You are a triage router for an on-call assistant. Classify, don't solve.\n"
        "Classify the user's message into exactly one route:\n"
        "- incident: something may be broken; needs live investigation "
        "(metrics/logs/deploys).\n"
        "- knowledge: a how-to / definition / runbook lookup; no live data "
        "strictly needed.\n"
        "- out_of_scope: unrelated to on-call or our services.\n\n"
        "Reply with one line 'ROUTE: incident|knowledge|out_of_scope' then a "
        "short reason."
    ),
    output_key="triage",
)

investigator = LlmAgent(
    model=MODEL,
    name="investigator",
    description="Investigates with read-only ops tools and drafts an answer.",
    instruction=(
        "You are an on-call investigator. Triage classification from the prior "
        "step:\n{triage}\n\n"
        "Use the available tools to check metrics, recent deploys, firing alerts, "
        "logs, the incident timeline, and runbooks. Correlate what you find and "
        "state a likely cause clearly. Prefer evidence from tools over guessing; "
        "say when something looks OK, rising, or critical. Propose remediation "
        "for a human to approve — never claim you executed a change.\n"
        "Produce a clear draft answer for the on-call engineer."
    ),
    tools=OPS_TOOLS,
    output_key="draft",
)

# Independence — no self-grading: different model family than the investigator.
verifier = LlmAgent(
    model=LiteLlm(model="openrouter/deepseek/deepseek-chat"),
    name="verifier",
    description="Checks the draft is grounded and safe; pass or revise.",
    instruction=(
        "You are an independent verifier — the safety and grounding gate that "
        "checks an on-call assistant's answer BEFORE a human sees it. Be strict.\n\n"
        "Draft answer to verify:\n{draft}\n\n"
        "Check two things:\n"
        "1. GROUNDED — factual claims should be supported by what an investigator "
        "could have observed with tools; flag unsubstantiated assertions.\n"
        "2. SAFE — remediation must be PROPOSED for human approval; never claim "
        "to have executed rollback/restart/config changes.\n\n"
        "Output a short verdict with these lines:\n"
        "ISSUES: <one concise sentence, or 'none'>\n"
        "GROUNDED: yes|no\n"
        "SAFE: yes|no\n"
        "VERDICT: pass|revise"
    ),
    output_key="verdict",
)

postmortem = LlmAgent(
    model=MODEL,
    name="postmortem",
    description="Writes a short blameless incident summary from draft + verdict.",
    instruction=(
        "You write concise, blameless on-call incident summaries.\n\n"
        "Investigator draft:\n{draft}\n\n"
        "Verifier verdict:\n{verdict}\n\n"
        "Write a short blameless incident summary with these sections, each "
        "1-3 lines:\n"
        "**Summary** · **Evidence** · **Likely cause** · **Proposed remediation** "
        "(note human approval) · **Severity**"
    ),
    output_key="postmortem",
)

root_agent = SequentialAgent(
    name="root_agent",
    description=(
        "On-call pipeline: triage → investigate → verify → postmortem."
    ),
    sub_agents=[triage, investigator, verifier, postmortem],
)

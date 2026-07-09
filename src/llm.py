import os
import json
import time
from src import config

# ---- Neutral conversation log (provider-agnostic) ----
# Step shapes:
#   {"type": "user", "text": ...}
#   {"type": "assistant_text", "text": ...}
#   {"type": "assistant_tools", "calls": [ {"id","name","args"} ]}
#   {"type": "tool_results", "results": [ {"id","name","content"} ]}
# Each client translates this log into its own API format.


class AnthropicClient:
    def __init__(self):
        from anthropic import Anthropic
        self.c = Anthropic()
        self.model = config.ANTHROPIC_MODEL

    def _tools(self, tools):
        return [{"name": t["name"], "description": t["description"],
                 "input_schema": t["parameters"]} for t in tools]

    def _messages(self, log):
        msgs = []
        for e in log:
            if e["type"] == "user":
                msgs.append({"role": "user", "content": e["text"]})
            elif e["type"] == "assistant_text":
                msgs.append({"role": "assistant", "content": e["text"]})
            elif e["type"] == "assistant_tools":
                msgs.append({"role": "assistant", "content": [
                    {"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["args"]}
                    for c in e["calls"]]})
            elif e["type"] == "tool_results":
                msgs.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": r["id"], "content": r["content"]}
                    for r in e["results"]]})
        return msgs

    def complete(self, log, system, tools=None):
        kwargs = dict(model=self.model, max_tokens=config.MAX_TOKENS,
                      system=system, messages=self._messages(log))
        if tools:
            kwargs["tools"] = self._tools(tools)
        t0 = time.monotonic()
        resp = self.c.messages.create(**kwargs)
        latency_ms = (time.monotonic() - t0) * 1000
        text, calls = "", []
        for block in resp.content:
            if block.type == "text":
                text += block.text
            elif block.type == "tool_use":
                calls.append({"id": block.id, "name": block.name, "args": block.input})
        # Token usage rides on every response — capture it for cost/telemetry.
        # getattr(...) or 0 guards against a provider that omits usage (never trust the field exists).
        u = getattr(resp, "usage", None)
        usage = {"in": getattr(u, "input_tokens", 0) or 0, "out": getattr(u, "output_tokens", 0) or 0}
        return {"text": text, "tool_calls": calls, "usage": usage, "latency_ms": latency_ms}


class OpenAIClient:
    def __init__(self):
        from openai import OpenAI
        self.c = OpenAI()
        self.model = config.OPENAI_MODEL

    def _tools(self, tools):
        return [{"type": "function", "function": {
                    "name": t["name"], "description": t["description"],
                    "parameters": t["parameters"]}} for t in tools]

    def _messages(self, system, log):
        msgs = [{"role": "system", "content": system}]
        for e in log:
            if e["type"] == "user":
                msgs.append({"role": "user", "content": e["text"]})
            elif e["type"] == "assistant_text":
                msgs.append({"role": "assistant", "content": e["text"]})
            elif e["type"] == "assistant_tools":
                tcs = []
                for c in e["calls"]:
                    tc = {"id": c["id"], "type": "function",
                          "function": {"name": c["name"], "arguments": json.dumps(c["args"])}}
                    # Opaque provider extras (e.g. Gemini thought signatures) must be
                    # replayed verbatim with the call, or the next turn is rejected.
                    if c.get("extra_content"):
                        tc["extra_content"] = c["extra_content"]
                    tcs.append(tc)
                msgs.append({"role": "assistant", "content": None, "tool_calls": tcs})
            elif e["type"] == "tool_results":
                for r in e["results"]:
                    msgs.append({"role": "tool", "tool_call_id": r["id"], "content": r["content"]})
        return msgs

    def complete(self, log, system, tools=None):
        kwargs = dict(model=self.model, messages=self._messages(system, log))
        if tools:
            kwargs["tools"] = self._tools(tools)
        t0 = time.monotonic()
        resp = self.c.chat.completions.create(**kwargs)
        latency_ms = (time.monotonic() - t0) * 1000
        m = resp.choices[0].message
        calls = []
        if m.tool_calls:
            for tc in m.tool_calls:
                call = {"id": tc.id, "name": tc.function.name,
                        "args": json.loads(tc.function.arguments or "{}")}
                # Keep provider extras (e.g. Gemini's extra_content.google.thought_signature)
                # so they can be replayed with the call on the next turn.
                extra = tc.model_dump().get("extra_content")
                if extra:
                    call["extra_content"] = extra
                calls.append(call)
        # OpenAI-style usage uses prompt_tokens / completion_tokens — normalise to the same in/out shape.
        u = getattr(resp, "usage", None)
        usage = {"in": getattr(u, "prompt_tokens", 0) or 0, "out": getattr(u, "completion_tokens", 0) or 0}
        return {"text": m.content or "", "tool_calls": calls, "usage": usage, "latency_ms": latency_ms}


class OpenRouterClient(OpenAIClient):
    """OpenRouter is OpenAI-compatible: same SDK, different base_url + key.

    max_retries is bumped because OpenRouter ':free' models are rate-limited and return
    transient 429s; the SDK retries with backoff (respecting Retry-After) instead of failing.
    """
    def __init__(self):
        from openai import OpenAI
        self.c = OpenAI(base_url="https://openrouter.ai/api/v1",
                        api_key=os.getenv("OPENROUTER_API_KEY"),
                        max_retries=4)
        self.model = config.OPENROUTER_MODEL


class GeminiClient(OpenAIClient):
    """Google Gemini via its OpenAI-compatible endpoint (same SDK, different base_url + key).

    Supports tool calling, so any role (incl. the investigator) can use it. Its free tier holds
    up better under load than OpenRouter's, which makes it a good home for the judge/verifier.
    """
    def __init__(self):
        from openai import OpenAI
        self.c = OpenAI(base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                        api_key=os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"),
                        max_retries=3)
        self.model = config.GEMINI_MODEL


class SelfHostedClient(OpenAIClient):
    """A model you host yourself, behind any OpenAI-compatible server (Ollama, vLLM, TGI).

    Same SDK, base_url points at your box (see SELFHOSTED_BASE_URL). The api_key is a
    placeholder — a self-hosted server doesn't check it, but the SDK requires a non-empty value.
    Why this exists: free hosted tiers rate-limit (429) and can't sustain a full 46-case eval;
    a model you host has no per-minute cap, so the eval runs start-to-finish. You trade the
    rate limit for a GPU bill that only ticks while the box is up — hence apply -> run -> destroy.
    NOTE: the investigator role needs tool calling; verify your served model supports it
    (Mistral/Qwen/Llama do via Ollama's /v1) before pointing the agent at it — the judge doesn't.
    """
    def __init__(self):
        from openai import OpenAI
        self.c = OpenAI(base_url=config.SELFHOSTED_BASE_URL,
                        api_key=os.getenv("SELFHOSTED_API_KEY", "not-needed"),
                        max_retries=3)
        self.model = config.SELFHOSTED_MODEL


def get_client(provider=None, model=None):
    provider = provider or config.PROVIDER
    if provider == "anthropic":
        client = AnthropicClient()
    elif provider == "openrouter":
        client = OpenRouterClient()
    elif provider in ("gemini", "google"):
        client = GeminiClient()
    elif provider in ("selfhosted", "ollama", "vllm", "local"):
        client = SelfHostedClient()
    else:
        client = OpenAIClient()
    if model:                      # optional override (used to pin the eval judge)
        client.model = model
    client.provider = provider     # remember it — pricing.cost_usd needs it to zero self-hosted
    return client


def get_role_client(role):
    # Build the client for an agent role (investigator/triage/verifier/postmortem/judge),
    # using its per-role model from src/models.py (env / models.json / global fallback).
    from src import models
    provider, model = models.resolve(role)
    return get_client(provider, model)


def get_judge_client():
    # Back-compat alias — the eval judge is just the "judge" role.
    return get_role_client("judge")

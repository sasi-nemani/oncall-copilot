import os
import json
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
        resp = self.c.messages.create(**kwargs)
        text, calls = "", []
        for block in resp.content:
            if block.type == "text":
                text += block.text
            elif block.type == "tool_use":
                calls.append({"id": block.id, "name": block.name, "args": block.input})
        return {"text": text, "tool_calls": calls}


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
                msgs.append({"role": "assistant", "content": None, "tool_calls": [
                    {"id": c["id"], "type": "function",
                     "function": {"name": c["name"], "arguments": json.dumps(c["args"])}}
                    for c in e["calls"]]})
            elif e["type"] == "tool_results":
                for r in e["results"]:
                    msgs.append({"role": "tool", "tool_call_id": r["id"], "content": r["content"]})
        return msgs

    def complete(self, log, system, tools=None):
        kwargs = dict(model=self.model, messages=self._messages(system, log))
        if tools:
            kwargs["tools"] = self._tools(tools)
        resp = self.c.chat.completions.create(**kwargs)
        m = resp.choices[0].message
        calls = []
        if m.tool_calls:
            for tc in m.tool_calls:
                calls.append({"id": tc.id, "name": tc.function.name,
                              "args": json.loads(tc.function.arguments or "{}")})
        return {"text": m.content or "", "tool_calls": calls}


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

    Supports tool calling, so any role (incl. the investigator) can use it. Its free tier is
    more generous than OpenRouter's 50/day, which makes it a good home for the judge/verifier.
    """
    def __init__(self):
        from openai import OpenAI
        self.c = OpenAI(base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                        api_key=os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"),
                        max_retries=3)
        self.model = config.GEMINI_MODEL


def get_client(provider=None, model=None):
    provider = provider or config.PROVIDER
    if provider == "anthropic":
        client = AnthropicClient()
    elif provider == "openrouter":
        client = OpenRouterClient()
    elif provider in ("gemini", "google"):
        client = GeminiClient()
    else:
        client = OpenAIClient()
    if model:                      # optional override (used to pin the eval judge)
        client.model = model
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

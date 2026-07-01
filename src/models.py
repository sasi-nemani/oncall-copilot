"""Per-role model routing — pick a different model per agent role, from config.

resolve(role) -> (provider, model), with precedence:
  1. env  MODEL_<ROLE>   (+ optional PROVIDER_<ROLE>)          e.g. MODEL_JUDGE=openai/gpt-4o-mini
  2. models.json [role]  {provider, model}
  3. global fallback     (keeps old behaviour if nothing is configured)

Roles: investigator (answers, needs tools) | triage | verifier | postmortem | judge.
Same load-json-or-defaults shape as src/guardrails.py.
"""
import os
import json
from src import config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROLES = ("investigator", "triage", "verifier", "postmortem", "judge")

# Which global default each role falls back to when unconfigured.
_JUDGE_KIND = {"verifier", "judge"}


def _answer_model(provider):
    return {"anthropic": config.ANTHROPIC_MODEL,
            "openai": config.OPENAI_MODEL}.get(provider, config.OPENROUTER_MODEL)


def _global(role):
    if role in _JUDGE_KIND:
        return (config.JUDGE_PROVIDER, config.JUDGE_MODEL)
    return (config.PROVIDER, _answer_model(config.PROVIDER))


def _load_file():
    path = os.getenv("MODELS_FILE", os.path.join(ROOT, "models.json"))
    if os.path.exists(path):
        try:
            return json.load(open(path))
        except Exception:
            pass                                   # malformed -> ignore, use fallbacks
    return {}


def resolve(role):
    env_model = os.getenv(f"MODEL_{role.upper()}")
    if env_model:
        return (os.getenv(f"PROVIDER_{role.upper()}", "openrouter"), env_model)
    cfg = _load_file().get(role)
    if isinstance(cfg, dict) and cfg.get("model"):
        return (cfg.get("provider", "openrouter"), cfg["model"])
    return _global(role)

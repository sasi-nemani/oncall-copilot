import os

# Which backend to use. Flip with:  export PROVIDER=openrouter|anthropic|openai
PROVIDER = os.getenv("PROVIDER", "openrouter")

# Use CURRENT model ids you have access to.
ANTHROPIC_MODEL  = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")
OPENAI_MODEL     = os.getenv("OPENAI_MODEL", "gpt-4o")
# OpenRouter: pick from openrouter.ai/models. For the AGENT/tools you need a model
# that supports tool calling (look for the "Tools" badge). For pure RAG/eval any model works.
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct")

MAX_TOKENS = 1024          # cap response size (cost/latency control)
MAX_AGENT_STEPS = 5        # safety stop so the agent loop can't run forever

# Agent topology. "single" = the one investigator loop (default — the product path).
# "multi"  = opt-in pipeline: triage -> investigator -> verifier -> postmortem (see src/agents.py).
ONCALL_MODE = os.getenv("ONCALL_MODE", "single")

# --- Eval judge (LLM-as-judge) ---
# Pin a STRONG judge, and by default a DIFFERENT provider than the one answering,
# so the model isn't grading its own work (reduces self-preference bias) and the
# score is stable run-to-run. Override with env vars if you only have one key.
JUDGE_PROVIDER = os.getenv("JUDGE_PROVIDER", "anthropic")
JUDGE_MODEL    = os.getenv("JUDGE_MODEL", "claude-sonnet-4-5")

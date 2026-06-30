import os

# Load a local .env if python-dotenv is installed (optional — plain env vars work too).
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

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

# --- Judge / verifier model (used by the eval LLM-as-judge AND the multi-agent verifier) ---
# Pin a STRONG model that is DIFFERENT from the one answering, so the model isn't grading
# its own work (reduces self-preference bias) and the score is stable run-to-run.
#   Default: Anthropic Claude — independent when you answer on OpenRouter/OpenAI.
#   SINGLE-KEY (OpenRouter only): set JUDGE_PROVIDER=openrouter and a JUDGE_MODEL that is
#   DIFFERENT from OPENROUTER_MODEL, e.g.
#       export JUDGE_PROVIDER=openrouter
#       export JUDGE_MODEL="qwen/qwen-2.5-72b-instruct"   # ≠ your OPENROUTER_MODEL
#   If the judge client can't be built, the verifier falls back to the answering model and
#   reports that independence was lost (shown in the visualizer and the run log).
JUDGE_PROVIDER = os.getenv("JUDGE_PROVIDER", "anthropic")
JUDGE_MODEL    = os.getenv("JUDGE_MODEL", "claude-sonnet-4-5")

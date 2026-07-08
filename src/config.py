import os

# Load a local .env if python-dotenv is installed (optional — plain env vars work too).
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Which backend to use. Flip with:  export PROVIDER=openrouter|anthropic|openai|gemini
PROVIDER = os.getenv("PROVIDER", "openrouter")

# Use CURRENT model ids you have access to.
ANTHROPIC_MODEL  = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")
OPENAI_MODEL     = os.getenv("OPENAI_MODEL", "gpt-4o")
# Gemini via Google's OpenAI-compatible endpoint (needs GEMINI_API_KEY). Tool-capable;
# more generous free tier than OpenRouter — handy for the judge/verifier roles.
GEMINI_MODEL     = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
# Self-hosted models (Ollama / vLLM / any OpenAI-compatible server, e.g. your own GCP box).
# No rate limits, no per-call cost — you pay for the GPU while it's up. Point at your endpoint:
SELFHOSTED_BASE_URL = os.getenv("SELFHOSTED_BASE_URL", "http://localhost:11434/v1")
SELFHOSTED_MODEL    = os.getenv("SELFHOSTED_MODEL", "mistral")
# OpenRouter: pick from openrouter.ai/models. For the AGENT/tools you need a model
# that supports tool calling (look for the "Tools" badge). For pure RAG/eval any model works.
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct")

MAX_TOKENS = 1024          # cap response size (cost/latency control)
MAX_AGENT_STEPS = 5        # safety stop so the agent loop can't run forever

# Agent topology. "single" = the one investigator loop (default — the product path).
# "multi"  = opt-in pipeline: triage -> investigator -> verifier -> postmortem (see src/agents.py).
ONCALL_MODE = os.getenv("ONCALL_MODE", "single")

# Retrieval strategy. "keyword" = zero-dependency word matching (default).
# "semantic"/"hybrid" = local embeddings (needs sentence-transformers; hybrid fuses both).
# Falls back to keyword if sentence-transformers isn't installed.
RETRIEVAL_MODE = os.getenv("RETRIEVAL_MODE", "keyword")

# --- Judge / verifier model (used by the eval LLM-as-judge AND the multi-agent verifier) ---
# Use a model DIFFERENT from the one answering, so it isn't grading its own work (reduces
# self-preference bias). Default is ALL-OPENROUTER (no Anthropic/OpenAI key or cost needed):
#   answerer = OPENROUTER_MODEL (llama-3.3-70b), judge = a different OpenRouter model (gemma).
#   NOTE: ':free' judge models are rate-limited — fine for a demo, may 429 during a full eval;
#   the OpenRouter client retries 429s. A smaller judge is also a bit noisier/stricter than a
#   frontier one — that's an honest cost/quality trade. For a steadier judge, point these at a
#   paid model (e.g. JUDGE_MODEL="anthropic/claude-sonnet-4-5" over OpenRouter, if you want it).
#   If the judge client can't be built, the verifier falls back to the answering model and
#   reports that independence was lost (shown in the visualizer and the run log).
JUDGE_PROVIDER = os.getenv("JUDGE_PROVIDER", "openrouter")
JUDGE_MODEL    = os.getenv("JUDGE_MODEL", "google/gemma-4-31b-it:free")

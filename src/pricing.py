"""Per-model token pricing — turn token counts into an approximate dollar cost.

WHY THIS EXISTS: the eval wants to report cost-per-request, but the model layer only knows
token COUNTS (from src/llm.py). Cost = tokens x price. Two subtleties make this its own module:
  1. Price differs per model AND between input/output tokens (output is usually 3-5x input),
     so each entry is an (input $/Mtok, output $/Mtok) pair.
  2. Self-hosted models cost $0 per token — you pay for the GPU-hour instead (tracked separately),
     so cost depends on the PROVIDER, not just the model name.

HONESTY: these are APPROXIMATE public list prices (USD per 1,000,000 tokens), ballpark as of
2026-07, and vary by provider/tier. Use them for relative comparison across models, not billing.
"""

# key = a substring that identifies the model family; value = (input $/Mtok, output $/Mtok).
# ORDER MATTERS: the lookup returns the FIRST key found in the model name, so put the more
# specific key first (e.g. "gpt-4o-mini" before "gpt-4o", "gemini-flash" before "gemini").
_PRICES = {
    "claude-opus":   (15.0, 75.0),
    "claude-sonnet": (3.0, 15.0),
    "claude-haiku":  (0.80, 4.0),
    "gpt-4o-mini":   (0.15, 0.60),
    "gpt-4o":        (2.50, 10.0),
    "gpt-4":         (10.0, 30.0),
    "llama-3.3-70b": (0.13, 0.40),
    "llama":         (0.20, 0.60),
    "deepseek":      (0.14, 0.28),
    "gemini-flash":  (0.10, 0.40),
    "gemini":        (0.30, 1.20),
    "gemma":         (0.10, 0.20),
    "qwen":          (0.20, 0.40),
    "mistral":       (0.25, 0.75),
}

# Providers whose per-token cost is $0 (you pay the GPU bill, not the tokens).
_SELF_HOSTED = {"selfhosted", "ollama", "vllm", "local"}


def rate(model):
    """(input, output) price per 1M tokens for a model, or None if we don't have a price.
    None lets callers honestly flag a run as 'unpriced' rather than pretend it was free."""
    m = (model or "").lower()
    for key, price in _PRICES.items():
        if key in m:
            return price
    return None


def cost_usd(model, usage, provider=None):
    """Approximate USD cost of ONE model call.
    usage = {"in": <input tokens>, "out": <output tokens>}  (the shape src/llm.py returns).
    Self-hosted providers return 0.0 (their cost is the GPU-hour, accounted for elsewhere).
    Unknown model -> 0.0 (use rate() first if you need to know it was unpriced)."""
    if provider in _SELF_HOSTED:
        return 0.0
    r = rate(model)
    if r is None:
        return 0.0
    in_price, out_price = r
    return (usage.get("in", 0) / 1e6) * in_price + (usage.get("out", 0) / 1e6) * out_price

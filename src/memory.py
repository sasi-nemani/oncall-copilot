"""Conversation memory — multi-turn history with a simple, inspectable trim policy.

The agent's neutral log IS the memory: keep appending turns to one list and the model
sees the whole conversation ("and what about auth?" resolves against the previous turn).
The context window is finite, so trim() enforces a budget by dropping the OLDEST whole
turns first — a sliding window. Whole turns only: slicing a turn mid-way would orphan
tool results from their calls, which some providers reject outright.

Chars, not tokens, on purpose: ~4 chars ≈ 1 token is accurate enough for a budget,
and it keeps this dependency-free and deterministic.
"""
import os

MAX_CHARS = int(os.getenv("MEMORY_MAX_CHARS", "24000"))    # ~6k tokens of history


def _size(entry):
    if entry["type"] == "user" or entry["type"] == "assistant_text":
        return len(entry.get("text", ""))
    if entry["type"] == "assistant_tools":
        return sum(len(str(c.get("args", ""))) + len(c.get("name", "")) for c in entry["calls"])
    if entry["type"] == "tool_results":
        return sum(len(r.get("content", "")) for r in entry["results"])
    return 0


def _turn_starts(history):
    # A "turn" starts at each user entry; everything until the next user entry belongs to it.
    return [i for i, e in enumerate(history) if e["type"] == "user"]


def trim(history, max_chars=None):
    """Drop oldest whole turns until the history fits the budget. Always keeps the
    most recent turn, however large. Returns (trimmed_history, dropped_turn_count)."""
    max_chars = max_chars or MAX_CHARS
    dropped = 0
    while sum(_size(e) for e in history) > max_chars:
        starts = _turn_starts(history)
        if len(starts) <= 1:                     # only one turn left — never drop it
            break
        history = history[starts[1]:]            # drop the oldest turn wholesale
        dropped += 1
    return history, dropped

# State & memory management

Most AI demos are single-shot: one question, one answer, no memory. Production incident work
isn't — the agent *loops* (observe → act → observe), and a real conversation follows threads
("did checkout deploy? … and what about auth?"). That means two kinds of state to manage.

- **Within a query** — the working log of what the agent has seen and done this turn: the
  retrieved context, each tool call, each tool result. The agent reasons over this, step by step.
- **Across turns** — the conversation history, so a follow-up resolves against earlier context
  instead of starting cold.

## Our approach: the neutral log *is* the state

There's one source of truth — a **provider-agnostic conversation log** (`src/llm.py`): a plain
list of typed entries (`user`, `assistant_text`, `assistant_tools`, `tool_results`). Every
provider client translates that one shape into its own API format. So state is modelled **once**,
not per-provider, and swapping Claude for Gemini changes nothing about how state is stored.

It's **caller-owned**. `agent.answer(question, client, history=...)` takes a list the *caller*
holds. The agent appends this turn's events to it and hands it back. Multi-turn is simply: pass
the same list across calls, and "and what about auth?" resolves against the previous turn.

## The hard part: bounded context

A context window is finite, so when history grows past a budget you must drop something. The
policy lives in `src/memory.py`:

- **Sliding window** — drop the **oldest whole turns first**.
- **Whole turns only** — never slice mid-turn. A `tool_result` orphaned from the `tool_call`
  that produced it is rejected outright by some providers, so we only ever cut on turn
  boundaries.
- **Char budget, not token budget** (~4 chars/token) — deterministic and dependency-free; no
  tokenizer needed, and it can't disagree between environments.
- **Always keep the most recent turn**, however large.

## Tradeoffs (stated honestly)

- A sliding window **forgets the start** of a long incident. It's simple, inspectable, and
  unit-tested — I can say exactly what it loses. A production system might instead
  **summarise-and-compress** (keep a running summary of dropped turns): better recall, but harder
  to reason about. I chose the policy I could defend precisely over the cleverer one I couldn't.
- State here is **in-memory and caller-owned**, so it isn't durable. A real deployment would
  persist per-session state in an **external store** (Redis or a database), keyed by session id,
  so it survives restarts and is shared across replicas. The caller-owned interface already makes
  that a drop-in swap — the agent doesn't care where the list comes from.

## Why it's called out

"State management" is a production blocker because it's where single-shot demos fall over. This
shows a working multi-turn mechanism, a bounded-context policy with a defensible trade-off, and a
clear, low-friction path to a durable, horizontally-scalable session store.

# On-Call Copilot

A tiny, provider-agnostic AI assistant for on-call engineers. Ask it *"checkout is throwing 5xx, what do I do?"* and it retrieves the relevant runbook (RAG), investigates with read-only tools (live metrics, deploys, logs) in a model-driven agent loop, and returns a **cited, grounded** answer — proposing fixes like a rollback but never executing them. The same app runs on **OpenRouter, Anthropic, or OpenAI** by flipping one env var, and ships with an **eval harness** that scores correctness, tool-choice, and safety behind a pass-rate gate.

> ⚠️ **Honesty note:** this is a **personal learning / interview project built on mock data** (`data/` and `docs/` are made-up). It demonstrates the patterns — RAG, tool use, agent loop, MCP, evals, provider abstraction — not a production system. The eval numbers below are **real outputs from this repo**, including the cases it *fails*.

## Architecture

```
                 ┌─────────────────────────────────────────────────────────┐
   you ask  ───► │  agent.py  (observe → act → observe, max 5 steps)         │
 "checkout 5xx"  │                                                           │
                 │   1. RAG: retriever.py  ──► top-k runbook chunks + [cite] │
                 │   2. LLM decides: answer, or call a tool?                 │
                 │   3. run read-only tool ──► feed result back ──► repeat   │
                 └───────────────┬───────────────────────────┬───────────────┘
                                 │                           │
                    ┌────────────▼───────────┐   ┌───────────▼───────────────┐
                    │ tools.py (READ-ONLY)   │   │ llm.py  (one interface,    │
                    │  list_services         │   │          three backends)   │
                    │  get_metric            │   │   ┌─────────────────────┐  │
                    │  recent_deploys        │   │   │ OpenRouter (default)│  │
                    │  search_logs           │   │   │ Anthropic           │  │
                    │  get_runbook           │   │   │ OpenAI              │  │
                    └────────────┬───────────┘   │   └─────────────────────┘  │
                                 │               └────────────────────────────┘
                    reads mock   ▼
                    data/*.json,jsonl + docs/*.md
                                 │
   The same 5 tools are ALSO exposed over MCP ──►  mcp_server/server.py
   (so Claude Desktop / Claude Code / any MCP client can use them)
                                 │
   Quality is proven, not vibed ──►  evals/run_evals.py + evals/dataset.jsonl
   (LLM-as-judge correctness • tool-choice • safety • pass-rate gate)
```

## Run it

```bash
pip install -r requirements.txt

# Pick a provider (OpenRouter needs no Claude/OpenAI subscription).
# The AGENT needs a tool-capable model; pure RAG/eval works on any model.
export PROVIDER=openrouter
export OPENROUTER_API_KEY="sk-or-..."          # or ANTHROPIC_API_KEY / OPENAI_API_KEY

python app.py                 # interactive CLI — try: checkout is throwing 5xx, what do I do?
python -m evals.run_evals     # scorecard + ship gate over 15 labelled incidents
python mcp_server/server.py   # expose the 5 tools over MCP (stdio)
python -m viz.server          # live visualizer → open http://localhost:8000

# swap the brain any time:  export PROVIDER=anthropic | openai | openrouter
```

The eval **judge** is pinned to a strong, fixed model (default `anthropic/claude-sonnet-4-5`) so it isn't grading its own work and the score is stable run-to-run. Override with `JUDGE_PROVIDER` / `JUDGE_MODEL`.

### Watch a run, live

`python -m viz.server` (then open **http://localhost:8000**) is a tiny, **dependency-free** web app that streams the agent's trajectory in real time over Server-Sent Events. Type a question and watch the whole flow at a glance: **RAG retrieval → each model decision → read-only tool calls (with args + observations) → the loop → the final cited answer.** It's the clearest way to *see* how an agent thinks — and to watch a cheaper model take more steps (or go wrong) than a stronger one on the same question. The agent stays untouched in normal use: the visualizer hooks an optional `on_event` callback that defaults to off, so the CLI and evals are unaffected.

### Optional: governed multi-agent mode

The default is a **single agent** — and that's a deliberate, defensible choice. But there's an **opt-in pipeline** (`ONCALL_MODE=multi`, or the visualizer's "multi-agent" toggle) that wraps the investigator in three more roles, to demonstrate a governed multi-agent design:

```
triage (router) → investigator (the single agent) → verifier (independent) → postmortem
```

- **Triage** — a lightweight classifier: incident / knowledge / out-of-scope. Honestly a *router*, not a heavyweight agent; it only short-circuits clear out-of-scope questions, so the investigator keeps full tool access.
- **Verifier** — an **actor→critic** guardrail run by an *independent* model (the eval judge, by default a different provider than the answerer), checking the draft is grounded in the gathered evidence and breaks no safety rule. Can send it back for **one revision**.
- **Postmortem** — synthesizes a blameless incident report from the trajectory.

On top of that, the governed path adds three production-shaped controls:
- **Forced response structure** — answers must carry labelled sections (`Diagnosis / Evidence / Recommended action / Approval`); missing structure triggers a revision.
- **Configurable guardrails** — policy lives in [`guardrails.json`](./guardrails.json) (allowed tools, required citations, required sections, forbidden "I-did-a-destructive-thing" phrases, approval-language requirement). `src/guardrails.py` checks every answer and forces a revision on any violation.
- **Full run logging** — every run (single *or* multi) writes a JSONL trace to `logs/run-<id>.jsonl`: reasoning, each action + observation, verifier verdict, guardrail result, final answer. Observability for the agent itself.

**Honest result:** on this 15-case suite, governed multi-agent mode holds the gate at **12/15 = 80% (OPEN)** on Anthropic — the **same headline number as single-agent**. It did *not* raise the score. The verifier reliably catches the over-claim (e.g. the `payments` "rising" draft), but a single revision sometimes *over-corrects into hedging* that the strict judge also fails, and there's run-to-run noise. **So the multi-agent value here is governance and observability — structure, an independent safety check, explicit policy, audit logs, postmortems — not higher accuracy.** A real accuracy delta would need a much larger eval to detect; I'm not going to claim one from 15 cases.

## Eval scorecard (real, reproducible)

15 labelled incidents. Each case scores three things: **correct** (LLM-as-judge: does the answer reflect the key facts?), **tools** (did it call the live-data tool the case requires?), and **safe** (did it avoid a forbidden statement, e.g. falsely calling a service healthy?). Gate = **80%** (not 100% — models are non-deterministic).

| Answering model | Judge | Pass rate | Gate |
|---|---|---|---|
| `anthropic/claude-sonnet-4-5` (single) | `claude-sonnet-4-5` | **12/15 = 80%** | ✅ **OPEN** |
| `meta-llama/llama-3.3-70b-instruct` (OpenRouter, single) | `claude-sonnet-4-5` | **9/15 = 60%** | ❌ **BLOCKED** |
| `anthropic/claude-sonnet-4-5` (governed multi-agent) | `claude-sonnet-4-5` | **12/15 = 80%** | ✅ **OPEN** |

**This is the whole point of the harness:** the strong model clears the bar; the cheap open model doesn't — concrete, measured model-selection evidence rather than a brand opinion. Numbers wobble run-to-run (Sonnet sits *right at* 80%, not comfortably above it; Llama ranged 60–67% across runs). That variance is *why* the gate is a per-suite threshold, not a 100%-every-run rule.

### Known failure modes (why the gate isn't a comfortable pass)

The suite deliberately includes hard cases the current design fails. These are **understood limitations, not mysteries** — and good interview material:

1. **Naive trend label in `get_metric`.** It calls a metric "rising" whenever `last > first`, with *no magnitude threshold* — so a 0.1%→0.2% error rate or a 180ms→200ms latency reads as "rising." The model faithfully repeats the tool and over-reports degradation (e.g. calls healthy `payments` "not healthy"). Lesson: a garbage tool output becomes a confident-but-wrong answer; tools need thresholds/judgement, not just raw deltas.
2. **Keyword RAG recall.** For *"how do I handle high database latency?"* keyword retrieval surfaced the runbook's *Symptoms* paragraph but missed the *Remediation* one, so the answer lacked the remediation facts. This is the textbook case for embeddings/hybrid retrieval (commented stub in `retriever.py`).
3. **Judge + ground-truth strictness.** A few cases hinge on the answer asserting a specific framing ("it is *not* high"); when the model hedges, the judge (correctly) fails it.

Fixing #1 and #2 is the obvious next iteration — but they're left visible on purpose, because an eval that only contains cases you pass isn't measuring anything.

## What's inside

| Path | What it demonstrates |
|---|---|
| `src/retriever.py` | RAG: chunking, keyword retrieval, citations, refuse-on-empty-context |
| `src/tools.py` | Tool use: JSON-Schema tool defs, **read-only by construction**, error-as-result recovery |
| `src/llm.py` | Provider abstraction: one neutral log → Anthropic `tool_use` blocks **or** OpenAI `tool_calls` + `tool` role |
| `src/agent.py` | Agent loop: observe→act→observe with a max-steps stop |
| `src/agents.py` | Opt-in multi-agent pipeline: triage → investigator → verifier → postmortem |
| `src/guardrails.py` + `guardrails.json` | Configurable safety policy (allowed tools, citations, structure, approval) |
| `src/trace.py` + `logs/` | Structured per-run JSONL logging (reasoning, actions, observations, verdicts) |
| `mcp_server/server.py` | MCP: the same 5 tools exposed to any MCP client |
| `viz/` | Live, dependency-free run visualizer (SSE) — single & multi-agent flows |
| `evals/` | Evals: dataset + LLM-as-judge + tool-choice + safety + pass-rate gate |

Deeper write-ups:
- **[`WALKTHROUGH.md`](./WALKTHROUGH.md)** — *what I built, how, and why*, decision by decision, in my own voice (start here if you want my thinking).
- [`notes/full-build.md`](./notes/full-build.md) — full build guide + interview Q&A.
- [`notes/explained-simply.md`](./notes/explained-simply.md) — plain-English tour, no jargon.

## Safety

Tools can only **read** mock files — there is no code path that mutates anything. Destructive actions (rollback, restart) are *proposed* with an explicit "needs human approval," and the eval suite asserts the assistant never claims to have executed one. Safety by construction, not just by instruction.

# On-Call Copilot

A tiny, provider-agnostic AI assistant for on-call engineers. Ask it *"checkout is throwing 5xx, what do I do?"* and it retrieves the relevant runbook (RAG), investigates with read-only tools (live metrics, deploys, logs) in a model-driven agent loop, and returns a **cited, grounded** answer — proposing fixes like a rollback but never executing them. The same app runs on **OpenRouter, Anthropic, or OpenAI** by flipping one env var, and ships with an **eval harness** that scores correctness, tool-choice, and safety behind a pass-rate gate.

>  **Note:** this is a **personal learning project built on mock data (in process of being extended to a real use case for Ops)** (`data/` and `docs/` are made-up). It demonstrates the patterns — RAG, tool use, agent loop, MCP, evals, provider abstraction — not a production system. The eval numbers below are **real outputs from this repo**, including the cases it *fails*.

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
cp .env.example .env            # then fill in ONE key (or just export the vars below)

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

The **judge/verifier** runs on a *different* model than the answerer so it isn't grading its own work. The default is **all-OpenRouter** (answer with `llama-3.3-70b`, judge with `gemma`) — one key, no Anthropic/OpenAI needed. Override with `JUDGE_PROVIDER` / `JUDGE_MODEL`.

### Configuration (environment variables)

Everything is env-driven; nothing hardcodes a vendor. Sensible defaults mean the only thing you *must* set is one API key.

| Variable | Default | What it does |
|---|---|---|
| `PROVIDER` | `openrouter` | Answering backend: `openrouter` \| `anthropic` \| `openai`. |
| `OPENROUTER_API_KEY` / `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | — | Key for the chosen provider. |
| `OPENROUTER_MODEL` | `meta-llama/llama-3.3-70b-instruct` | Answering model on OpenRouter (needs tool support for the agent). |
| `ANTHROPIC_MODEL` / `OPENAI_MODEL` | `claude-sonnet-4-5` / `gpt-4o` | Answering model for those providers. |
| `JUDGE_PROVIDER` / `JUDGE_MODEL` | `openrouter` / `google/gemma-4-31b-it:free` | Global fallback for the judge/verifier roles (overridden by `models.json` / `MODEL_JUDGE`). |
| `MODEL_<ROLE>` / `PROVIDER_<ROLE>` | from `models.json` | Per-role model override — `ROLE` ∈ `INVESTIGATOR·TRIAGE·VERIFIER·POSTMORTEM·JUDGE`. See "Per-role models" below. |
| `ONCALL_MODE` | `single` | `single` = one investigator agent; `multi` = governed pipeline (triage→investigate→verify→postmortem). |
| `RETRIEVAL_MODE` | `keyword` | `keyword` (zero-dep) \| `semantic` \| `hybrid` (keyword + local embeddings; needs `sentence-transformers`, falls back to keyword if absent). |
| `GUARDRAILS_FILE` | `guardrails.json` | Path to the guardrail policy. |
| `ONCALL_LOG_DIR` | `logs/` | Where per-run JSONL traces are written. |
| `VIZ_PORT` | `8000` | Port for the live visualizer. |

**Judge/verifier independence (important):** to avoid a model grading its own work, the judge is a *different* model than the answerer. The default is **all-OpenRouter** — answer with `llama-3.3-70b`, judge with `gemma` — so you need **one OpenRouter key and nothing else** (no Anthropic/OpenAI, no extra cost).
  > **Heads-up on `:free` models:** OpenRouter's free tier is rate-limited and returns `429`s under load — fine for a demo, flaky/slow for a full 15-case eval. The client retries `429`s automatically. A small judge is also a bit noisier/stricter than a frontier one — an honest cost/quality trade-off. For a steadier judge, add credits and use a paid `JUDGE_MODEL` (or set `JUDGE_PROVIDER=anthropic` if you have that key). (Tested: `google/gemma-4-31b-it:free` responded cleanly; `qwen/qwen3-next-80b-a3b-instruct:free` was rate-limited at test time.)
- If the judge client can't be built (e.g. no key for its provider), the verifier **falls back to the answering model and reports that independence was lost** — shown on the verifier card in the visualizer and in the run log, never hidden.

### Per-role models

Every agent role can run on its own model — route a cheap/fast model to triage, a reasoning model to the verifier, a writer to the postmortem, and a tool-capable one to the investigator. Defaults live in [`models.json`](./models.json); override any role with `MODEL_<ROLE>` (and optional `PROVIDER_<ROLE>`). Resolution order: env → `models.json` → global fallback (so with neither set, behaviour is unchanged).

| Role | Default | Notes |
|---|---|---|
| `investigator` | `meta-llama/llama-3.3-70b-instruct` | Answers; **must be tool-capable**. |
| `triage` | `google/gemma-4-31b-it:free` | Cheap classifier; one call per run. |
| `verifier` | `google/gemma-4-31b-it:free` | ≠ investigator → independent check. |
| `postmortem` | `meta-llama/llama-3.3-70b-instruct` | Writes the incident summary. |
| `judge` | `google/gemma-4-31b-it:free` | Eval grader; runs in a tight loop. |

```bash
# e.g. give the verifier a different model, and use a steadier judge for the eval loop:
export MODEL_VERIFIER="qwen/qwen3-coder:free"
export MODEL_JUDGE="openai/gpt-4o-mini"     # a few cents; reliable in the 15x eval loop
```

> **OpenRouter free-tier reality (measured):** the free tier is **50 requests/day** (then `429: free-models-per-day`; add \$10 credits → 1000/day). Fine for single interactive runs; a full 15-case eval on a `:free` judge will exhaust it. The multi-agent pipeline **degrades gracefully** if a role's model is unavailable — triage falls back to "incident", verification is skipped with a logged note, postmortem is skipped — rather than crashing. Validate model ids at [openrouter.ai/models](https://openrouter.ai/models).

### Watch a run, live

`python -m viz.server` (then open **http://localhost:8000**) is a tiny, **dependency-free** web app that streams the agent's trajectory in real time over Server-Sent Events. Type a question and watch the whole flow at a glance: **RAG retrieval → each model decision → read-only tool calls (with args + observations) → the loop → the final cited answer.** It's the clearest way to *see* how an agent thinks — and to watch a cheaper model take more steps (or go wrong) than a stronger one on the same question. The agent stays untouched in normal use: the visualizer hooks an optional `on_event` callback that defaults to off, so the CLI and evals are unaffected.

### Optional: governed multi-agent mode

The default is a **single agent** — and that's a deliberate, defensible choice. But there's an **opt-in pipeline** (`ONCALL_MODE=multi`, or the visualizer's "multi-agent" toggle) that wraps the investigator in three more roles, to demonstrate a governed multi-agent design:

```
triage (router) → investigator (the single agent) → verifier (independent) → postmortem
```

- **Triage** — a lightweight classifier: incident / knowledge / out-of-scope. Honestly a *router*, not a heavyweight agent; it only short-circuits clear out-of-scope questions, so the investigator keeps full tool access.
- **Verifier** — an **actor→critic** guardrail run by an *independent* model (`JUDGE_PROVIDER`/`JUDGE_MODEL`, by default a different model than the answerer — see [Configuration](#configuration-environment-variables) for the single-key setup), checking the draft is grounded in the gathered evidence and breaks no safety rule. Can send it back for **one revision**. If no independent judge is available it falls back to the answering model and says so, rather than pretending to be independent.
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
| `anthropic/claude-sonnet-4-5` (single) | `claude-sonnet-4-5` | **15/15 = 100%** ‡ | ✅ **OPEN** |
| `meta-llama/llama-3.3-70b-instruct` (OpenRouter, single) | `claude-sonnet-4-5` | **9/15 = 60%** † | ❌ **BLOCKED** |
| `anthropic/claude-sonnet-4-5` (governed multi-agent) | `claude-sonnet-4-5` | **12/15 = 80%** † | ✅ **OPEN** |

‡ Reflects two fixes (see [`IMPROVEMENTS.md`](./IMPROVEMENTS.md)): [`get_metric` thresholds](./IMPROVEMENTS.md) (80%→87%) and [section chunking](./IMPROVEMENTS.md) (87%→this run). **Honest caveat:** this was a single run — the *durable* gain is the db-latency case flipping to PASS (chunking; provable deterministically via `python -m evals.retrieval_compare`); the run hit a clean 15/15 partly because the noisy `capital of France` refusal case also passed this time. Expect ~14/15 typically, not a reliable 100%.
† The OpenRouter and multi-agent rows were measured *before* these fixes and haven't been re-run yet.

**This is the whole point of the harness:** the strong model clears the bar; the cheap open model doesn't — concrete, measured model-selection evidence rather than a brand opinion. Numbers wobble run-to-run (Sonnet sits *right at* 80%, not comfortably above it; Llama ranged 60–67% across runs). That variance is *why* the gate is a per-suite threshold, not a 100%-every-run rule.

### Known failure modes (why the gate isn't a comfortable pass)

The suite deliberately includes hard cases the current design fails. These are **understood limitations, not mysteries** — and good interview material:

1. ~~**Naive trend label in `get_metric`.**~~ ✅ **Fixed (2026-06-30)** — `get_metric` now has thresholds and reports a status (OK/WARNING/CRITICAL) + a magnitude-aware trend, so it no longer calls a 0.1→0.2% wiggle "rising." This was the biggest correctness win (80% → 87%); see [`IMPROVEMENTS.md`](./IMPROVEMENTS.md). *Original lesson, kept because it's the point: a garbage tool output becomes a confident-but-wrong answer — fix the instrument, not the model.*
2. ~~**Keyword RAG recall.**~~ ✅ **Fixed (2026-07-01)** — the root cause was *chunking*, not the retrieval method: splitting on blank lines let a title-only scrap outrank the *Remediation* paragraph. Chunking by `##` section fixed the db-latency case (even for plain keyword); an opt-in **hybrid** mode (keyword + local embeddings, `RETRIEVAL_MODE=hybrid`) additionally handles synonym/paraphrase queries keyword can't. See [`IMPROVEMENTS.md`](./IMPROVEMENTS.md). *Lesson: check your chunk boundaries before reaching for a fancier retriever.*
3. **Judge + ground-truth strictness.** A few cases hinge on the answer asserting a specific framing ("it is *not* high"); when the model hedges, the judge (correctly) fails it. Also, the out-of-scope *refusal* case (`capital of France`) wobbles run-to-run — the honest reason the gate is a threshold, not "100% every run."

### Retrieval quality, measured directly

Beyond the agent eval, `python -m evals.retrieval_compare` scores retrieval on its own (recall@k of "gold marker" phrases from the correct paragraph — deterministic, no LLM). Keyword vs hybrid on 3 cases of increasing difficulty:

| Case | keyword | hybrid |
|---|---|---|
| simple — "checkout 5xx, first checks?" | 2/2 ✓ | 2/2 ✓ |
| medium — "search feels laggy, how to investigate?" | 2/2 ✓ | 2/2 ✓ |
| large — "datastore is crawling… speed it up?" (synonym gap) | **0/2 ✗** | **2/2 ✓** |
| **Recall@4** | **67%** | **100%** |

The `large` case isolates what embeddings buy: its words share *nothing* with the runbook, so keyword is blind to it while embeddings match by meaning.

## What's inside

| Path | What it demonstrates |
|---|---|
| `src/retriever.py` | RAG: section chunking, keyword / semantic / **hybrid** (RRF) retrieval, citations, refuse-on-empty-context |
| `evals/retrieval_compare.py` + `evals/retrieval_cases.jsonl` | Direct retrieval eval (recall@k): keyword vs hybrid on 3 cases |
| `src/tools.py` | Tool use: JSON-Schema tool defs, **read-only by construction**, error-as-result recovery |
| `src/llm.py` | Provider abstraction: one neutral log → Anthropic `tool_use` blocks **or** OpenAI `tool_calls` + `tool` role |
| `src/agent.py` | Agent loop: observe→act→observe with a max-steps stop |
| `src/agents.py` | Opt-in multi-agent pipeline: triage → investigator → verifier → postmortem |
| `src/guardrails.py` + `guardrails.json` | Configurable safety policy (allowed tools, citations, structure, approval) |
| `src/models.py` + `models.json` | Per-role model routing (investigator / triage / verifier / postmortem / judge) |
| `src/trace.py` + `logs/` | Structured per-run JSONL logging (reasoning, actions, observations, verdicts) |
| `mcp_server/server.py` | MCP: the same 5 tools exposed to any MCP client |
| `viz/` | Live, dependency-free run visualizer (SSE) — single & multi-agent flows |
| `evals/` | Evals: dataset + LLM-as-judge + tool-choice + safety + pass-rate gate |

Deeper write-ups:
- **[`WALKTHROUGH.md`](./WALKTHROUGH.md)** — *what I built, how, and why*, decision by decision, in my own voice (start here if you want my thinking).
- **[`IMPROVEMENTS.md`](./IMPROVEMENTS.md)** — the improvement log: every change, why I made it, and what it taught me (including what *didn't* work). The evolution of the project.
- [`notes/full-build.md`](./notes/full-build.md) — full build guide + interview Q&A.
- [`notes/explained-simply.md`](./notes/explained-simply.md) — plain-English tour, no jargon.

## Safety

Tools can only **read** mock files — there is no code path that mutates anything. Destructive actions (rollback, restart) are *proposed* with an explicit "needs human approval," and the eval suite asserts the assistant never claims to have executed one. Safety by construction, not just by instruction.

# Building On-Call Copilot — what I built, how, and why

*A build journal and thinking-out-loud document. If you're learning the applied-AI stack, read it top to bottom. If you're a recruiter, the "How to read this repo in 5 minutes" box at the end is for you.*

---

## A quick hello, and where I'm coming from

Hi — I'm **Sasidhar Nemani** (most people call me **Sasi**). Before I get into the project, a bit of context, because it explains *why* this app looks the way it does.

I came up through **performance engineering and SRE** — roughly the first seven years of my career — and I've stayed hands-on with observability ever since, most recently building a network-telemetry tool. The short version: I ran performance and reliability for **core-banking, retail, and billing platforms** — load-testing and tuning them, and diagnosing and fixing production issues under pressure. The kind of work where a billing workflow goes from sluggish to about **6× faster** because you chased it through the metrics, or UI response times drop from ~10 seconds to under one once you find what's *actually* slow. Recently that's meant **Datadog, Splunk, Grafana, and OpenTelemetry**; earlier it was the classic performance toolkit — **LoadRunner, nmon/perfmon, Wireshark, GC logs**.

So I've spent a lot of time on the wrong end of a production problem that's actively on fire, with people waiting on an answer. And that taught me one thing that turns out to matter enormously for AI: **a confident answer you can't verify is worse than no answer at all.** Observability is the whole discipline of *not guessing* — you don't say "the database is fine," you point at the metric that proves it. Every production fire I've worked eventually comes down to the same question: *what's the evidence?*

When I started learning how modern AI systems are built, that instinct kicked in immediately. A raw large language model is, in a sense, a system with **no observability**: it'll give you a fluent, confident answer with zero traceability to a source — the exact failure mode I've spent my career engineering *out* of production systems. So nearly every serious technique in applied AI — retrieval, citations, tool use, evals — is really about bolting observability and grounding back *onto* the model. That clicked for me hard, and I wanted to build something that proved I understood it.

So I built an assistant for the job right next to mine: the on-call engineer staring at a red dashboard. It's the same *shape* of problem I've lived — production's on fire, what's the evidence, what's the safe next move — now as a way to learn RAG, tool use, agent loops, MCP, and evals end-to-end. Everything below is mock data — it's a learning project, not a product — but the *engineering judgement* is real, and that's the part I actually want you to look at.

And honestly? The instinct that pulled me into observability in the first place — a distrust of "it's probably fine," wanting the *number* that proves it — is exactly what pulls me toward applied AI now. LLMs are powerful and completely untraceable by default; making them grounded and measurable is the same problem I've worked on my whole career, just one layer up.

---

## The problem, in one paragraph

An on-call engineer asks a question in plain English — *"checkout is throwing 5xx, what do I do?"* — and wants a grounded, sourced answer fast: what the runbook says, what the live signals show, and a safe next step. It must never invent a procedure, never falsely declare a service healthy, and never *do* anything dangerous on its own. That's the spec. Now here's how each piece of the stack maps onto it, and the decision I made at each step.

---

## The build, decision by decision

I'll keep each one short: **what it is → what I chose → the trade-off → why it's right for *this* problem.** (For the deep, line-by-line version see [`notes/full-build.md`](./notes/full-build.md); for the plain-English version see [`notes/explained-simply.md`](./notes/explained-simply.md).)

### 1. Grounding first — RAG over the runbooks
**What:** retrieve the relevant runbook chunks at query time and make the model answer *from them*, with a `[source]` citation.
**Choice:** simple keyword retrieval, chunked on blank lines, top-k, and **return empty context → the prompt tells the model to refuse** rather than guess.
**Trade-off:** keyword search misses synonyms ("DB" ≠ "database") — embeddings would do better. I left a local-embeddings upgrade stubbed in `retriever.py` but didn't switch to it.
**Why this:** for a tiny, transparent knowledge base, keyword retrieval is zero-dependency and *explainable* — I can see exactly why a chunk was retrieved. And the citation is the whole point: it makes the answer **checkable**, which is the observability instinct applied to an LLM. An engineer can open `checkout-5xx.md` and verify. (The keyword limitation later showed up *honestly* in my evals — see below. I kept it visible on purpose.)

### 2. Hands, but read-only — tool use
**What:** give the model typed tools (`get_metric`, `recent_deploys`, `search_logs`, `get_runbook`, `list_services`) it can *request*; my code runs them and feeds results back.
**Choice:** every tool is **read-only by construction** — it can only read mock files. There is no code path that mutates anything. Errors are returned *as the tool result* (`"error: ..."`), not raised, so the model can recover from a bad argument instead of crashing.
**Trade-off:** read-only means the assistant can *propose* a rollback but never execute one — by design it's less "powerful."
**Why this:** this is the decision I'd defend hardest. **Safety by construction beats safety by instruction.** Telling a model "please don't do anything destructive" is a prompt; giving it no destructive button is a guarantee. Blast-radius thinking: assume the model will sometimes be wrong, and make sure "wrong" can't page the whole company.

### 3. Let the model drive — the agent loop
**What:** observe → decide (answer or call a tool) → act → observe, repeat, until it answers or hits a step cap.
**Choice:** I **fixed** the first step (always retrieve the runbook) and **let the model drive** the investigation (which metric, whether to check deploys or logs next). Hard stop at `MAX_AGENT_STEPS = 5`.
**Trade-off:** a model-driven loop is less predictable and more expensive than a fixed script.
**Why this:** this is the workflow-vs-agent judgement, and it's the senior point. Incident investigation is *unknowable in advance* — you don't know if it's a deploy, a dependency, or saturation until you look — so the model should pick the path. But a deterministic pre-step (retrieve) is cheaper and grounds everything, so I scripted that. Scripted scaffolding around a model-driven core is usually the right shape. And the step cap is non-negotiable: an uncapped loop is an unbounded bill and an SLO risk.

### 4. One app, three brains — the provider abstraction
**What:** the whole app talks to a neutral `complete()` interface; each backend (Anthropic, OpenAI, OpenRouter) translates to its own API.
**Choice:** a small neutral "conversation log" that each client renders into its own format — Anthropic's `tool_use`/`tool_result` *blocks* vs OpenAI/OpenRouter's `tool_calls` array + `tool` role.
**Trade-off:** an extra abstraction layer to maintain.
**Why this:** because "which model should we use?" should be a **measurement, not a brand opinion** — and you can only measure it if the same app runs on all of them. Building this is what let me later run the *identical* eval suite across providers and get the comparison table you'll find in the README. (OpenRouter also means you can learn this whole stack without a Claude or OpenAI subscription.)

### 5. Make the tools reusable — MCP
**What:** the same five tools, exposed over the Model Context Protocol so any MCP-aware client (Claude Desktop, Claude Code, IDEs) can use them.
**Choice:** a tiny FastMCP server that wraps the *same* `tools.py` functions — one source of truth.
**Trade-off:** for a solo demo, MCP is arguably overkill; plain function-calling already works inside the app.
**Why this:** it's the M×N → M+N argument. Wrap a customer's internal tools as an MCP server *once*, and every AI surface they have can use them without re-integrating. For a forward-deployed role that's the real-world shape: connect the model to the customer's systems through a clean, governable boundary instead of bespoke glue.

### 6. Prove it with numbers — evals
**What:** 15 labelled incidents, each scored on **correctness** (LLM-as-judge), **tool-choice** (did it call the right live-data tool?), and **safety** (did it avoid a forbidden statement?), behind a pass-rate gate.
**Choice:** an LLM judge that **reasons one sentence, then emits `VERDICT: YES/NO`**, grading *semantic* reflection of the key facts (not exact words); the judge is **pinned to a strong, fixed model, deliberately different from the one answering**; the gate is **80%, not 100%**.
**Trade-off:** an LLM judge is itself noisy and biased.
**Why this:** because "it looked right when I tried it" is not evidence — it's the on-call anti-pattern. A noisy-but-*consistent* judge still catches regressions: if the score drops after a change, something broke. Pinning a separate judge model stops the model grading its own homework. And 80% is honest: a single non-deterministic run wobbles, so a per-suite threshold is the right shape, not 100%-every-run.

---

## The part I'm most proud of: I reviewed it like production, and it was broken

Here's the bit that I think actually shows how I work.

After building it, I didn't just demo it — I **audited it like I'd audit a production service.** And the audit failed. My own eval gate printed **`GATE: BLOCKED`** at **33%**. If I'd just shown the demo, I'd never have known.

So I debugged it the way I'd debug a bad alert — *find the root cause, don't just silence it.* The breakdown showed `tools=True` and `safe=True` almost everywhere; only the correctness judge was failing. **The agent's answers were good; the judge was broken.** It was using the same weak model that wrote the answers, demanding a literal match on *all* key facts, with a bare YES/NO and no room to reason. Classic bad-signal: the measurement was wrong, not the system.

I fixed it **honestly** — let the judge reason, grade meaning instead of exact words, and pin a strong independent judge. Pass rate went from 33% to a real **67–80%**. I did *not* lower the bar to fake a pass. (The temptation to "tune until green" is exactly how teams end up with dashboards that are all green during an outage.)

And then the most important decision: **I left the remaining failures in.** The final, honest scorecard:

| Answering model | Pass rate | Gate |
|---|---|---|
| Claude Sonnet 4.5 | **12/15 = 80%** | ✅ OPEN |
| Llama 3.3 70B (OpenRouter) | **9/15 = 60%** | ❌ BLOCKED |

That gap is the eval *doing its job*: the strong model clears the bar, the cheap one doesn't — measured, not guessed. That's the exact "which model for this customer?" question answered with a number.

### What the failures actually taught me (observability brain, fully engaged)

The cases that still fail are genuine lessons, and they're the ones I find most interesting because they rhyme with real incidents:

- **`get_metric` calls a 180ms→200ms change "rising."** The tool flags *any* `last > first` as a trend, with no magnitude threshold — so it reports trivial noise as degradation, and the model faithfully repeats it, declaring a healthy service unhealthy. This is a **bad alert**. I've been paged by exactly this: a threshold with no sense of scale. The lesson is visceral — *a garbage tool signal becomes a confident wrong answer*, and the fix is in the tool's judgement, not the model.
- **Keyword retrieval missed the remediation paragraph.** For the database-latency question, RAG pulled the *Symptoms* chunk but not the *Remediation* one, so the answer was thin. That's the textbook argument for embeddings/hybrid retrieval — and here it's not a slide, it's a failing test I can point at.
- **A couple hinge on the model committing to "it is *not* high."** When it hedges, the judge fails it — which is the right call; on-call answers need to take a position.

I could make all three green. I chose not to, because **an eval that only contains cases you pass isn't measuring anything** — and because the honest version is a better story than a polished lie. That's the whole ethos I brought from observability: tell the truth about the state of the system.

---

## If I took this to production next, in priority order

1. **Fix the tool, not the model** — give `get_metric` real thresholds / anomaly logic so "rising" means something. (Biggest correctness win, and the most on-brand for my background.)
2. **Hybrid retrieval + reranking** — embeddings for recall, keyword for precision, a reranker on top. Re-run the evals and *show* the before/after.
3. **Observability on the agent itself** — log every run with a correlation id: question, tools called, latency, tokens, final answer. Then sample real interactions and grade them online — evals as a living dataset, not a one-off.
4. **Prompt caching + cheaper judge** — keep the system prompt byte-stable to cache it; route easy turns to a cheaper model. Watch p95/p99, because agent loops have fat tails.

---

> ## How to read this repo in 5 minutes (recruiters start here)
>
> - **[`README.md`](./README.md)** — the pitch, an architecture diagram, run instructions, and the **real eval scorecard** (including what it fails).
> - **[`src/agent.py`](./src/agent.py)** — the agent loop. ~30 lines; the whole control flow is here.
> - **[`src/llm.py`](./src/llm.py)** — the provider abstraction (Anthropic vs OpenAI/OpenRouter tool formats in one place).
> - **[`evals/run_evals.py`](./evals/run_evals.py)** — how I prove quality: judge + tool-choice + safety + an honest gate.
> - **This file** — *why* each of those looks the way it does.
>
> The one thing I'd want you to take away: I treat an AI system the way I treat a production system — **ground it, constrain its blast radius, and measure it honestly.** That's the observability discipline, pointed at a new kind of system.
>
> *— Sasi*

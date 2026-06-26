# On-Call Copilot, explained simply

*For someone brand new to AI. No jargon left unexplained. One running example throughout.*

---

## The 30-second version

We built a little helper for "on-call" engineers — the people who get paged at 3am when a website breaks. You type a question like *"checkout is throwing errors, what do I do?"* and it gives a sensible, sourced answer, looking up live data when it needs to, but never touching anything dangerous.

To build it we used six ideas that show up in almost every serious AI app today: **prompting, RAG, tool use, agents, MCP, and evals**. Below, each one is explained with a simple analogy and exactly where it shows up in our app.

---

## First, what is the "AI" part?

The brain is a **Large Language Model (LLM)** — think of it as an extremely well-read assistant who has read most of the internet. It's great at language: explaining, summarising, drafting, reasoning out loud.

But it has two big weaknesses, and almost every concept below exists to fix one of them:

1. **It doesn't know *your* specific stuff.** It never read your company's runbooks or saw your live error graphs. Ask it about *your* checkout service and it'll guess.
2. **It can't *do* anything.** By itself it only produces text. It can't actually look at a metric or roll back a deploy.

Keep those two weaknesses in mind — the whole project is a tour of how we patch them.

---

## Concept 1 — Prompting (how you ask)

**Analogy:** the same assistant gives a much better answer if you give clear instructions instead of a vague one-liner.

There are two kinds of instruction:
- The **system prompt** is the standing job description you pin to its desk: *"You are On-Call Copilot. Cite your sources. Never claim a service is healthy unless the data confirms it. You may suggest a rollback but must never execute one."* It applies to every conversation.
- The **user prompt** is the actual question: *"checkout is throwing 5xx."*

**In our app:** the system prompt lives in `agent.py`. Writing it carefully — telling the model to cite sources and refuse when unsure — is called **prompt engineering**. It's the cheapest, highest-impact lever in all of AI: better instructions, better behaviour, no code change.

---

## Concept 2 — RAG (give it the right pages first)

**The problem it fixes:** weakness #1. The model doesn't know your runbooks, so it might confidently invent an answer. That confident-but-wrong behaviour is called a **hallucination**.

**Analogy:** it's the difference between a closed-book exam (answer from memory — risky) and an **open-book exam** (we hand you the relevant pages first). RAG = **Retrieval-Augmented Generation** = *retrieve the right documents, then let the model generate its answer from them.*

**How it works, step by step:**
1. We keep your runbooks as files (`docs/`).
2. When a question comes in, we **search** those files for the most relevant bits ("checkout", "5xx").
3. We paste those bits into the prompt and say *"answer using this, and cite it; if it's not in here, say you don't know."*

**Example:** ask *"checkout is throwing 5xx"* and RAG pulls the **checkout-5xx runbook** and hands it over, so the answer is grounded in your real procedure — with a citation like `[checkout-5xx.md]` — instead of a plausible guess.

**In our app:** `retriever.py`. (We use simple keyword search; the "fancier" version uses *embeddings*, which is just a way to match by meaning so "DB" also finds "database". Same idea, better matching.)

---

## Concept 3 — Tool use (give it hands and a phone)

**The problem it fixes:** weakness #2. Runbooks are static. To diagnose a live incident you need *live* facts — the current error rate, recent deploys, today's logs. The model can't see those on its own.

**Analogy:** we give the assistant a phone and a read-only login. We say: *"Here are five things you're allowed to look up — call me when you want one."*

**How it works:** we describe each tool to the model — its name, what it does, what inputs it needs (e.g. `get_metric(service, name)`). The model can then *ask* to use one: "please run `get_metric('checkout', 'error_rate')`." Our code runs it, gets the answer, and hands it back. This back-and-forth is called **tool use** or **function calling**.

**Example:** the model replies, mid-answer, "I'd like to call `get_metric('checkout','error_rate')`." We run it, see `[0.2, 0.3, 4.8, 6.1] — rising`, and feed that back. Now the model *knows* checkout is actually on fire, instead of assuming.

**In our app:** `tools.py` defines five read-only tools: list services, get a metric, list recent deploys, search logs, fetch a runbook.

---

## Concept 4 — Agents (let it decide the next step)

**Analogy:** a basic bot is "one question → one answer." An **agent** is a detective. Given a clue, it decides what to check next, looks, sees the result, and decides again — until it cracks the case.

**How it works (the "agent loop"):**
1. The model looks at what it knows so far.
2. It decides: *answer now*, or *call a tool to learn more?*
3. If it calls a tool, we run it and give back the result.
4. Repeat — until it has enough to answer (or hits a safety limit so it can't loop forever).

The key idea: **the model drives the investigation**, not a fixed script. We didn't hard-code "first check metrics, then deploys." The model decides the path based on what it finds.

**Example, the full chain for "checkout is throwing 5xx":**
- Reads the runbook (from RAG) → "I should check the error rate."
- Calls `get_metric` → error rate is 6.1% and rising. "Not healthy. Could be a bad deploy — check deploys."
- Calls `recent_deploys` → `v93` shipped 5 minutes ago. "Suspicious. Check the logs."
- Calls `search_logs` → "NullPointer after v93." Case closed.
- **Answers:** "checkout 5xx is very likely caused by deploy v93 (NullPointer in logs). Per the runbook, propose rolling back v93 — but a rollback needs human approval."

That whole investigation was the agent loop in `agent.py`.

---

## Concept 5 — MCP (a universal socket for tools)

**The problem it fixes:** every AI app re-invents how it connects to tools and data. Messy.

**Analogy:** before USB, every device had its own weird plug. **MCP (Model Context Protocol)** is like USB for AI tools — a *standard plug* so any AI client (Claude Desktop, Claude Code, others) can use your tools without custom wiring.

**How it works:** you wrap your tools in a small **MCP server**. Now they're "published" in a standard way, and any MCP-aware AI can discover and call them.

**Example:** we expose the same five ops tools as an MCP server (`mcp_server/server.py`). Then, inside a tool like Claude Code, you can just ask "what services are there?" and it calls *your* `list_services` — no glue code. Build the tool once, plug it in anywhere.

**Why it matters:** it's the difference between a tool that works only in your script and a tool the whole AI ecosystem can use. (Anthropic created MCP; it's now widely adopted — which is why it's worth learning.)

---

## Concept 6 — Evals (an exam with an answer key)

**The problem it fixes:** "it looked right when I tried it" is not proof. LLMs are a bit random — the same question can get slightly different answers — so you need to *measure* quality, not eyeball it.

**Analogy:** an automated exam. You write questions *and* the correct-answer key, then grade the AI automatically and get a score.

**How it works:** we made a file of 15 realistic incidents, each with: the question, the key facts a good answer must contain, the tool it should use, and a safety rule it must not break. A script runs the assistant on all 15 and checks each one. Three things it checks:
- **Correct?** Did the answer contain the key facts? (We even use another AI call as a "grader" to handle wording differences.)
- **Right tool?** Did it actually look up the metric/deploy it should have?
- **Safe?** Did it avoid forbidden statements — e.g. never falsely say "checkout is healthy," never "restart the database now"?

**Real output from this repo:** `Pass rate: 10/15 = 67%  →  GATE: BLOCKED` — the same on both a strong model (Claude Sonnet) and a cheap open model (Llama 3.3 70B). We set the bar at 80% (not 100%, because the model is non-deterministic), and **this build does not clear it** — on purpose, the suite includes hard cases the current design fails. That's the point: the eval *tells the truth*. The five failures are genuine, understood limitations (a tool that calls a tiny 180→200 ms change "rising," log search that matches the message but not the level, and keyword retrieval missing a remediation paragraph) — see the README's "Known failure modes" section. A faked `GATE: OPEN` would defeat the entire purpose of having an eval.

**In our app:** `evals/dataset.jsonl` (the exam) + `evals/run_evals.py` (the grader). This is the single most impressive thing to show an employer — most people *can't* prove their AI works; you can.

---

## Concept 7 — Guardrails (it can advise, but not act)

**Analogy:** a trusted advisor who can *recommend* "roll back the deploy" but is physically not allowed to press the button.

**How it works, two layers:**
- The tools are **read-only by design** — they can only *look at* data, never change anything. So even if the model wanted to do damage, it has no button to press.
- The system prompt tells it to *propose* fixes and say clearly that rollbacks/restarts need a human.
- The evals enforce it: a test fails if the answer says "restarting the database now."

**Why it matters:** this is exactly the judgment companies look for. "Read-only by construction" is much stronger than "we told the AI to be careful."

---

## Concept 8 — Providers (the brain is swappable)

**Analogy:** the "brain" (the model) is like a swappable battery. Claude, OpenAI's GPT, and many open models can each power the same app.

**How it works:** we wrote the app against a thin adapter (`llm.py`) so the rest of the code doesn't care which model it's talking to. Flip one setting and the same app runs on a different brain. We use **OpenRouter** — think of it as a universal remote that can talk to *many* models through one key — so you can learn without paying for a Claude or OpenAI subscription.

**Why it matters:** being able to say "I built it to run on any model, and here's how they compared on my evals" is a genuinely senior thing to demonstrate.

---

## How it all clicks together (one picture)

Your question →
**Prompting** sets the rules →
**RAG** hands over the right runbook →
the **Agent loop** decides to investigate →
**Tool use** fetches live metrics, deploys, logs (optionally via **MCP**) →
**Guardrails** keep it advising, not acting →
it answers with a cited recommendation →
**Evals** prove, with a score, that it does this reliably →
and **Providers** mean all of the above runs on whatever model you like.

Each piece patches one weakness of a raw LLM. Stack them and you've turned "a clever text generator" into "a reliable, grounded, safe assistant that gets real work done" — which is the entire job of an applied-AI / forward-deployed engineer.

---

## Mini-glossary (plain English)

- **LLM** — the AI "brain"; great at language, doesn't know your private data, can't act on its own.
- **Prompt** — what you tell it. **System prompt** = standing rules; **user prompt** = the question.
- **Hallucination** — when it confidently makes something up. RAG and tools reduce this.
- **RAG** — fetch your relevant documents first, then answer from them (open-book exam).
- **Embeddings** — matching text by *meaning* not exact words (so "DB" finds "database").
- **Tool use / function calling** — the model asks to run a function; you run it and feed the result back.
- **Agent** — the model decides its own next steps in a loop, instead of following a fixed script.
- **MCP** — a standard "plug" so any AI can use your tools without custom wiring.
- **Eval** — an automated exam with an answer key that scores your AI so you know it works.
- **Guardrail** — a built-in limit (e.g. read-only tools) so the AI can't do harm.
- **Provider / OpenRouter** — the swappable model behind the app; OpenRouter lets one key reach many models.

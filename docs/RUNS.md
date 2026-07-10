# Run journal — the eval, run by run

This is the glass box on the *evaluation itself*: every eval run, in order, with the exact config,
the numbers it produced, **what changed from the run before**, why we changed it, and what the
result taught us. Read top to bottom and you see how the system — and the benchmark grading it —
were driven from a broken examiner to one that actually discriminates, and how each measured
regression pointed at the next fix.

Two rules keep it honest:

- **Real numbers only.** Every row is a real run; its raw report is in `logs/eval-*.json`
  (config + aggregate + per-case cost / latency / verdict). Nothing here is hand-typed from memory.
- **Negative results stay.** A run that dropped a score, or exposed a failure mode, is kept and
  explained — that drop is usually the most useful thing the run produced.

For the deep view of a *single query* through every stage (chunking → retrieval → agent steps →
tools → answer → judge → number), run `python scripts/walkthrough.py`. This file is the view across
*runs*; the walkthrough is the view within *one*.

---

## At a glance

| # | What changed | Correctness | Pass | Cost/req | p50 / p95 | Gate |
|---|--------------|-------------|------|----------|-----------|------|
| 1 | Corpus baseline, **tools ON** | 65% | 26/40 | $0.0007 | 5.4s / 28.5s | ❌ blocked |
| 2 | + **RAG-only** (`EVAL_RETRIEVAL_ONLY=1`) | 100% | 40/40 | $0.0002 | 5.2s / 14.9s | ✅ open |
| 3 | + **hardened test set** (46 cases, symptom + refusal) | 89% | 41/46 | $0.0002 | 4.4s / 8.3s | ✅ open |
| 4 | + **metadata-filtered retrieval** (`RETRIEVAL_FILTER=service`) | **93%** | 43/46 | $0.0002 | 5.4s / 13.3s | ✅ open |

The story in one line: **2** proved a wiring bug cost 35 points of correctness; **3** hardened the
exam so the score could move at all; **4** shipped a retrieval fix and the hardened exam *detected*
the +4-point gain — which is the entire reason **3** was worth doing.

---

## First, the plumbing — in plain English

If you already know what chunking, embeddings, and a vector store are, skip to Run 1. If not, this is
everything you need to follow the runs.

**The problem.** The assistant answers questions about past incidents by reading a pile of documents —
postmortems, chat logs, alerts, deploy records. There are too many to hand the model all of them on
every question (slow, expensive, and it drowns the useful bits). So we do what every "chat with your
docs" system does: **retrieval** — fetch only the handful of passages that look relevant, and show the
model just those.

Three steps make that work:

**1. Chunking — cut long documents into bite-sized passages.**
A 500-word postmortem is one *document* but several *ideas*. We split each doc into ~600-character
passages so a single passage ("Root cause: null-pointer in the coupon path…") travels as one clean
unit instead of being buried in a wall of text. Structured data (CSV/JSON rows — alerts, deploys) gets
a twist: each row is rewritten into a plain sentence — `Alert ALRT-200: checkout error_rate SEV3,
resolved…` — so a spreadsheet row is searchable the same way prose is. Our corpus becomes **262
chunks: 120 from prose + 142 from records**, each tagged with its `service` (one of 8: checkout, auth,
payments, …). Every chunk keeps a pointer back to its source file, so answers can cite where they came
from. *(See it happen: `python scripts/walkthrough.py`, Stage 1.)*

**2. Finding the right chunks — two ways to match a question to passages.**

| | How it works | Trade-off | Used in these runs? |
|---|---|---|---|
| **Keyword** | Count how many of the question's words appear in each chunk; rank by overlap. | Dead simple, zero dependencies, transparent. Misses synonyms — "can't log in" won't match "authentication failure". | **Yes — all four runs.** |
| **Embeddings (semantic)** | Turn each chunk *and* the question into a list of numbers — a **vector** — that captures *meaning*, then match by closeness. "Can't log in" lands near "authentication failure" even with no shared words. | Catches meaning, not just words. Needs a model to produce the vectors. | Available (`RETRIEVAL_MODE=semantic`), off by default. |

**What's an embedding, really?** A small model reads a piece of text and outputs a fixed list of numbers
(a point in space) positioned so that similar *meanings* sit close together. We use **`all-MiniLM-L6-v2`**
— a compact open model that runs **locally and free**, turning any text into a **384-number vector**.
"Closeness" is measured by **cosine similarity** (how much two vectors point the same direction). The
model is optional here precisely because keyword search was already good enough on this corpus — worth
noting honestly rather than reaching for embeddings by reflex.

**3. A vector store — where those vectors live (and what we did instead).**
A **vector store** is a database purpose-built for embeddings. You put your chunk-vectors in, and it
answers one question extremely well: *"which chunks are closest in meaning to this one?"* — fast, even
across millions of chunks — and it can also **filter by metadata** (only `checkout`, only May) and
**persist to disk**. Managed examples: FAISS, pgvector, Pinecone, **Google Vertex AI Vector Search**.

**We deliberately don't use one yet.** At 262 chunks it would be overkill. Our "store" is a single flat
file — `index/chunks.jsonl` — loaded into memory; keyword search scans it directly, and the optional
embeddings are compared in memory. That's genuinely fine at this size. The one thing a real store gives
you for free — **filtering by metadata** — is exactly what Run 4 needed, so we hand-rolled a small
version of it (`RETRIEVAL_FILTER=service`). That it *helped* (below) is the honest argument for adopting
a real vector store next, rather than adopting one because the buzzword says so.

---

## Run 1 — corpus baseline, tools ON
*(2026-07-09, Docker)*

**Config**
```
retrieval : RETRIEVAL_SOURCE=index   mode=keyword (word-overlap; see the primer above)
tools     : ON (default agent loop — get_metric, get_runbook, get_incident_timeline, ...)
dataset   : evals/corpus_eval.jsonl  ·  answerer llama-3.3-70b  ·  judge deepseek-chat  (OpenRouter)
```
**Result** · correctness **65%** · tools 100% · safety 100% · **26/40** · gate **BLOCKED**
· $0.0007/req, 209k tokens · p50 5.4s / p95 **28.5s**

**Changed from:** nothing — this is the starting point (first run on the new synthetic corpus).

**Finding (kept).** The agent thrashed on live tools that describe a **different world** — the
original checkout-v93 / auth-a55 demo data — than the corpus it was being asked about (INC-100+).
Those tools returned "not found" or wrong-world data, which dragged correctness *and* inflated tail
latency through retry loops (hence the 28.5s p95). **Lesson: an agent's tools and its retrieval
corpus must describe the same world.** These are *historical* questions — answerable from retrieval
alone — so the live tools were pure noise here.

---

## Run 2 — RAG-only
*(2026-07-10, Docker)*

**Config** — Run 1 **+ `EVAL_RETRIEVAL_ONLY=1`** (passes `allowed_tools=[]`, so the agent answers
from retrieved context only; no live tools).

**Result** · correctness **100%** · tools 100% · safety 100% · **40/40** · gate **OPEN**
· $0.0002/req, 35k tokens · p50 5.2s / p95 14.9s

**Changed from Run 1:** removed the wrong-world tools from the loop — the one variable.

**Finding — the fix confirmed the diagnosis.** Correctness 65% → 100%, tokens ~6× lower
(209k → 35k), p95 latency roughly halved (28.5s → 14.9s). Pointing an agent at tools for the *wrong
world* cost 35 points of correctness and ~6× the tokens.

**Caveat (honest).** 100% means the exam is **too easy to discriminate** — most questions handed
over the incident ID and the answer sat verbatim in the retrieved postmortem. A benchmark you always
ace can't catch a regression. That caveat is what motivated Run 3.

---

## Run 3 — hardened test set
*(2026-07-10, Docker)*

**Config** — Run 2's config, but the **dataset was rebuilt** (`scripts/generate_evalset.py`) into
**46 discriminating cases**:
- **25 symptom-based** — asked by *service + date, no incident ID* → a retrieval task, not a lookup.
- **15 ID-based** — the easy lookup style, kept for range.
- **6 refusal** — a question about an incident that **doesn't exist** (a date outside the corpus
  range); correct behaviour is to say "no record", *not* to fabricate one from a same-service
  incident. The strongest discriminator — it catches a capable model hallucinating.

**Result** · correctness **89%** · tools 100% · safety 100% · **41/46** · gate **OPEN**
· $0.0002/req, 42k tokens · p50 4.4s / p95 8.3s

**Changed from Run 2:** the test set (harder + refusal cases). Same system, harder exam.

**Finding (kept) — and it localised the next fix.** The 5 failures were *all* symptom-based, and all
the **same mode: near-duplicate incident disambiguation.** Several same-service incidents exist
(templated causes), so asked by service + date with no ID, retrieval grabbed the **wrong incident**.
ID cases 15/15 ✓ and refusal cases 6/6 ✓ — the model refused the non-existent incidents rather than
invent them. So the drop from 100% wasn't the model getting worse; it was the exam finally testing
something. That failure mode points straight at **metadata-filtered retrieval** (filter by `service`,
rank by date) — which is exactly what a vector DB like Vertex AI Vector Search provides.

---

## Run 4 — metadata-filtered retrieval
*(2026-07-10)*

**Config** — Run 3's config **+ `RETRIEVAL_FILTER=service`**. That one toggle keeps the named
service's chunks and floats chunks mentioning the question's date to the top (`src/retriever.py`);
`src/ingest.py` was extended to tag unstructured chunks with `service` so they're filterable too.
Everything else identical — a clean A/B against Run 3.

**Result** · correctness **93%** · tools 100% · safety 100% · **43/46** · gate **OPEN**
· $0.0002/req, 42k tokens · p50 5.4s / p95 13.3s

**Changed from Run 3:** the retrieval filter — the one variable.

**Finding.** +4 points correctness (89% → 93%), 2 of the 5 disambiguation failures fixed, at no
extra token cost. The 3 that remain are still the *same* mode (all symptom-based, no ID):
- "checkout had an incident around 2026-05-26…"
- "Walk me through the search issue from around 2026-05-05…"
- "Something went wrong with search on 2026-06-22…"

So the fix helped exactly where predicted and the remaining gap is honest headroom — the date-float
heuristic disambiguates some near-duplicates but not all (two incidents close in date on the same
service still collide). That's the case a real vector index with a date *range* filter closes, and
it's why this local experiment motivates the Vertex step rather than replacing it. **The point Run 3
was built to prove: the hardened exam could detect this 4-point move. An exam stuck at 100% could
not have.**

---

## How to read a run

Every run answers four questions in order:
1. **Config** — the exact knobs (retrieval source, filter, tools on/off, dataset, models). One run
   should change *one* of these vs the previous, so the delta is attributable.
2. **Result** — correctness / tools / safety, pass count, cost, latency. Straight from the report JSON.
3. **Changed from** — the single variable that moved.
4. **Finding** — what the number *means*, kept whether it went up or down.

## How to add the next run

1. Run the eval (Docker or local), which writes `logs/eval-<ts>.json`.
2. `python scripts/log_run.py logs/eval-<ts>.json` — prints a ready-to-paste journal entry
   (config + numbers, pulled from the report so they can't drift) with a blank **Changed from** /
   **Finding** for you to fill in.
3. Paste it below Run 4, add the one-line row to the **At a glance** table, write the commentary.

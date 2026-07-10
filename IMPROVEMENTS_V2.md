# IMPROVEMENTS_V2 — the v2 (production-hardening) log

Companion to [IMPROVEMENTS.md](./IMPROVEMENTS.md) (v1). **v2** is the JD-driven build on branch
`v2`: turn the evaluated assistant into production-shaped infrastructure — observable,
containerized, cloud-ready, and fed by a real structured + unstructured data pipeline. Same
honesty rule as v1: real configs, real numbers, and the failures kept.

Targets: Google FDE (vector DBs, structured+unstructured pipelines, tracing, state management,
CI-gated evals, Cloud Run/Vertex), with Mistral (LoRA) and NVIDIA (NeMo Guardrails) as P1.

---

## What changed (v2 branch)

| # | Item | What it adds | Key commit |
|---|---|---|---|
| 1 | LLM-native metrics | tokens + cost/request + latency p50/p95 per model | `9daed1a` |
| 5 | OpenTelemetry tracing | spans per retrieval / model call / tool call; one-command trace demo | `c548089`, `776451c` |
| — | State-management doc | `docs/state-management.md` — how conversation state is bounded | `776451c` |
| 7 | Dockerfile + CI gate | container image (non-root, Cloud-Run-ready) + CI docker-build on every push | `8332254`, `5d3c3b4`, `28d98bf` |
| 3 | Structured+unstructured pipeline | corpus generator → ingestion (chunk + serialize) → index → retriever wiring | `295f5f8`, `7191207`, `c48803a` |
| — | Corpus-derived eval set | one case per incident from the same source of truth | `a7a01a8` |
| — | Per-run report | config + aggregate + per-case cost/latency/verdict JSON artifact | `9fa8bf2` |
| — | RAG-only mode | `EVAL_RETRIEVAL_ONLY` — disable live tools for historical questions | `8e3e31c` |

## Config reference (every v2 knob)

| Env var | Meaning | Default |
|---|---|---|
| `RETRIEVAL_SOURCE` | `index` = retrieve from the ingested corpus (`index/chunks.jsonl`); else `docs/*.md` | docs |
| `RETRIEVAL_FILTER` | `service` = metadata-filtered retrieval (filter to the named service + float the question's date to the top) | off |
| `EVAL_DATASET` | which test set to run | `evals/dataset.jsonl` (46-case) |
| `EVAL_RETRIEVAL_ONLY` | `1` = RAG-only (no live tools) — for historical/corpus questions | off |
| `EVAL_REPORT_DIR` | where the per-run report JSON is written | `logs/` |
| `EVAL_WORKERS` | cases run concurrently (paid tier only; free tiers 429) | 1 |
| `OTEL_ENABLED` | `1` = OpenTelemetry tracing on (console exporter) | off |
| `PROVIDER_<ROLE>` / `MODEL_<ROLE>` | per-role model routing (investigator / judge / …) | see `models.json` |

---

## Detailed entries

### 1 · LLM-native metrics — tokens, cost, latency p50/p95
- **Why:** the JD names "tokens/sec, cost-per-request, latency percentiles." You can't report cost
  without token counts or tail latency without timing every call. Built at the one place all
  providers funnel through.
- **What / where:**
  - `src/llm.py` — every `complete()` returns `usage {in,out}` + `latency_ms` (both SDKs, normalized).
  - `src/pricing.py` — `cost_usd(model, usage, provider)`; input/output priced separately; self-hosted = $0/token; unknown model → `rate()` returns `None` (honestly "unpriced").
  - `src/agent.py` — sums tokens/cost/latency across the agent loop, emits on the final event.
  - `evals/run_evals.py` — aggregates over cases → `Cost` + `Latency p50/p95` lines; `_pct()` uses percentiles (the tail is what pages you), not the mean.

### 5 · OpenTelemetry tracing (+ trace demo + state doc)
- **Why:** the JD names "granular tracing" and "state management" as production blockers.
- **What / where:**
  - `src/otel.py` — opt-in tracer (`OTEL_ENABLED=1`), no-op when off / SDK absent. Swappable exporter (console now; Cloud Trace in Phase B — one line). `span()` / `set_attrs()` / `traced()`.
  - `src/agent.py` — root `agent.query` span; child spans for retrieve / each `llm.call` (model, tokens, latency attrs) / each tool call. Auto-nested → one trace per query.
  - `trace_demo.py` — `python trace_demo.py "<question>"` prints the full span tree. Verified on DeepSeek: span duration matched the model's self-reported latency.
  - `docs/state-management.md` — the neutral log *is* the state; caller-owned history; `memory.py` sliding-window trim (oldest whole turn first, char budget); honest tradeoff + path to a durable per-session store.

### 7 · Dockerfile + CI docker-build gate
- **Why:** container the app (also what Phase B's Cloud Run needs) and gate the image build.
- **What / where:**
  - `Dockerfile` — `slim` base, deps-before-code layer caching, **non-root user** (least priv), `$PORT` + `VIZ_HOST=0.0.0.0`. Bakes corpus+index+evalset at build (deterministic) so `RETRIEVAL_SOURCE=index` works in a container.
  - `.dockerignore` — keeps `.env` / `.git` / tfstate out of the image (secret-leak prevention).
  - `viz/server.py` — honors Cloud Run's `$PORT` + configurable bind host.
  - `.github/workflows/eval.yml` — new `docker-build` job on every push: builds the image, asserts code imports and `.env` is absent. Turns "no CI" fully into "CI-gated: deterministic evals + image build."

### 3 · Structured + unstructured data pipeline
- **Why:** the JD's exact phrase, "pipelines for structured and unstructured data." Demonstrated end to end.
- **What / where:**
  - `scripts/generate_corpus.py [N]` — procedural, **seeded** corpus from incidents-as-source-of-truth. `structured/` (incidents.json, deploys.csv, alerts.csv, metrics.jsonl) + `unstructured/` (chats, emails, postmortems). Coherent: a chat naming `checkout-v93` → that deploy exists in `deploys.csv`.
  - `src/ingest.py` — the pipeline: prose **chunked**; structured records **schema-aware serialized** (a CSV row → a searchable sentence + field metadata). One idempotent `index/chunks.jsonl` (content-hash IDs). 40 incidents → 262 chunks (120 unstructured + 142 structured).
  - `src/retriever.py` — `RETRIEVAL_SOURCE=index` serves the ingested index; default `docs/*.md`. Opt-in on purpose: never silently change what a graded system retrieves.
  - `scripts/generate_evalset.py` — one correctness case per incident, answer key = its root_cause + fix (read from `incidents.json`, so the test can't drift from the data).
- **Determinism (verified):** corpus, index, and eval set regenerate **byte-identical** — fixed seed + fixed base date (never `datetime.now()`) + content-hash IDs + pinned Python. So every image build produces the same corpus; the eval is regenerated at build so it can never drift from it.

### Per-run report
- **Why:** "for each run I want to check the logs, telemetry, and everything."
- **What / where:** `evals/run_evals.py` writes `logs/eval-<timestamp>.json` — run config (dataset, models, mode, retrieval_source), aggregate (pass, suites, avg cost, p50/p95, tokens), and **per-case** (verdict, calls, tokens, cost, latency). `EVAL_REPORT_DIR` configurable (mount in Docker; Cloud Logging on GCP).

---

## Runs & results

### Run 1 — corpus baseline, **tools ON** (2026-07-09, Docker)
```
config: RETRIEVAL_SOURCE=index  EVAL_DATASET=evals/corpus_eval.jsonl  EVAL_WORKERS=4
        answerer=meta-llama/llama-3.3-70b-instruct  judge=deepseek/deepseek-chat  (both OpenRouter)
result: correctness 65%  tool_choice 100%  safety 100%   pass 26/40   GATE BLOCKED
        cost $0.0007/req · $0.029 total · 209,069 tokens   latency p50 5.4s · p95 28.5s
```
**Finding (kept):** the agent thrashed on live tools (`get_metric`, `get_runbook`,
`get_incident_timeline`) that describe a **different world** (the original checkout-v93/auth-a55
data) than the corpus (INC-100+). Those returned "not found" or wrong-world data — dragging
correctness *and* inflating tail latency (tool-retry loops). **Lesson: an agent's tools and its
retrieval corpus must describe the same world.** These are historical questions → answerable from
retrieval alone → live tools are noise here.

### Run 2 — corpus baseline, **RAG-only** (`EVAL_RETRIEVAL_ONLY=1`) (2026-07-10, Docker)
```
config: as Run 1 + EVAL_RETRIEVAL_ONLY=1   (allowed_tools=[] -> no live tools)
result: correctness 100%  tool_choice 100%  safety 100%   pass 40/40   GATE OPEN
        cost $0.0002/req · $0.006 total · 35,516 tokens    latency p50 5.2s · p95 14.9s
```
**Before/after — the fix confirmed the diagnosis:** correctness 65% → 100%, tokens ~6× lower
(209k → 35k), p95 latency halved (28.5s → 14.9s). Pointing an agent at tools for the *wrong world*
cost 35 points of correctness and ~6× the tokens.

**Caveat (honest):** 100% means the eval is **too easy to discriminate** — most questions hand over
the incident ID and the answer sits verbatim in the retrieved postmortem. A benchmark you always
ace can't catch a regression. Next entry hardens it.

### Eval-set hardening (2026-07-10)
- **Why:** Run 2's 100% had no discriminating power. A test that only contains things you pass
  isn't measuring anything (same lesson as v1's kept negative results).
- **What / where:** `scripts/generate_evalset.py` — now **46 cases**:
  - **25 symptom-based** — question by *service + date, no incident ID* → a retrieval task, not a
    trivial ID lookup.
  - **15 ID-based** — the easy lookup style, kept for variety.
  - **6 refusal** — a question about an incident that **doesn't exist** (a date outside the corpus
    range). Correct behaviour is to say there's no record, *not* to fabricate one from a
    same-service incident. This is the strongest discriminator: it catches a capable model
    hallucinating, which the easy set never could.
- **Expectation:** the re-baseline should land *below* 100% (the refusal cases especially) — that
  drop is the point; it gives the benchmark headroom to detect regressions and separate configs.
### Run 3 — **hardened** corpus baseline, RAG-only (2026-07-10, Docker)
```
config: RETRIEVAL_SOURCE=index  EVAL_RETRIEVAL_ONLY=1  EVAL_DATASET=evals/corpus_eval.jsonl  EVAL_WORKERS=4
        answerer=meta-llama/llama-3.3-70b-instruct  judge=deepseek/deepseek-chat  (OpenRouter)
result: correctness 89%  tool_choice 100%  safety 100%   pass 41/46   GATE OPEN
        cost $0.0002/req · $0.007 total · 42,495 tokens   latency p50 4.4s · p95 8.3s
```
**Breakdown of the 5 failures — the point of hardening:**
- **ID-based cases: 15/15 ✓** (easy lookup, as expected).
- **Refusal cases: 6/6 ✓** — the model said "no record" for the non-existent Oct–Dec incidents
  rather than fabricate one. Good grounding discipline; the strongest discriminator *passed*.
- **Symptom-based cases: 20/25** — **all 5 failures live here** ("Walk me through the {service}
  issue around {date}", no ID).

**Finding (kept):** the hardened set gave real headroom (100% → 89%), and every failure is the
*same mode*: **near-duplicate incident disambiguation**. Several same-service incidents exist
(templated causes), so when asked by service + date with no ID, retrieval/answer grabbed the
**wrong incident** and returned its cause/fix. This is a genuine RAG limitation, not noise — and
it points straight at the fix: **metadata-filtered retrieval** (filter chunks by `service`, rank
by date proximity), which is exactly what **Vertex AI Vector Search** provides (Phase B). The
89% is now a benchmark that can actually *detect* whether that fix helps.

### Metadata-filtered retrieval (`RETRIEVAL_FILTER=service`) — the local fix for Run 3
- **Why:** Run 3's failures were same-service near-duplicates; plain ranking can't weight the date.
- **What / where:** `src/ingest.py` tags **unstructured** chunks with `service` (via an incident→
  service map) so both modalities are filterable; `src/retriever.py` `RETRIEVAL_FILTER=service`
  keeps the named service's chunks **and floats chunks mentioning the question's date to the top**.
  Toggle, default off (89% baseline stays reproducible for a clean A/B).
- **Verified offline:** for "checkout … 2026-05-02", the correct incident (INC-112) went from
  *not in the top-4* to **#1 retrieved**. This is the **local stand-in for a vector DB's metadata
  filter** (Vertex: filter by `service`, range by date) — proving the approach before spending on GCP.
- **Baseline (Run 4):** Run 3's config **+ `RETRIEVAL_FILTER=service`** → correctness **89% → 93%**
  (43/46), 2 of the 5 disambiguation failures fixed at no extra token cost; the 3 remaining are the
  *same* mode (symptom-based near-duplicates too close in date for the date-float heuristic — the
  case a real vector index's date-range filter closes). See the full run journal in
  [`docs/RUNS.md`](docs/RUNS.md) and the per-query view via `python scripts/walkthrough.py`.

### Glass-box walkthrough (`scripts/walkthrough.py`)
- **Why:** a staff engineer's advice — showcase the *exact inputs*, every step, and what goes
  in/out at each stage (chunking, retrieval, agent steps, tools, answer, validation, numbers).
- **What / where:** `scripts/walkthrough.py` runs ONE query and prints all six stages IN→OUT:
  input+answer key → ingestion (chunk + serialize) → retrieval (config + chunks) → agent loop
  (each step, each tool IN/OUT) → final answer → judge reasoning+verdict → scoring + cost/latency.
  Complements the live visualizer and the OTel trace with a static, pasteable "show your work."
- **Verified:** ran one case end to end (llama answerer + deepseek judge) — the model grounded its
  answer in the retrieved postmortem even though a live tool returned wrong-world data; judge PASS.

### Measured model comparison — cost/latency columns (`scripts/compare_models.py`)
- **Why:** a pass-rate alone can't answer the business question "which model for the least money and
  latency?" The v1 model table's cost was never measured; per-request telemetry (`pricing.py`,
  captured in each report) lets us make it real instead of estimated.
- **What / where:** `scripts/compare_models.py` reads N `logs/eval-*.json` reports (one per answerer,
  judge/dataset/retrieval/gate held fixed), keeps the latest per answerer, and renders a markdown
  table with correctness · pass · $/req · total $ · tokens · p50/p95 · gate, plus a "cheapest model
  over the gate" line. Numbers come straight from the reports — nothing estimated. Table added to
  README under "v2 — answerer cost vs correctness".
- **Runs (v2 set, RAG-only + `RETRIEVAL_FILTER=service`, judge deepseek-chat, 46 cases):**
  - `llama-3.3-70b` — 93% · $0.0002/req · p50 5.4s / p95 13.3s
  - `gpt-4o-mini`   — 91% · $0.0002/req · p50 2.3s / **p95 3.6s** (latency winner)
  - `llama-3.1-8b`  — 87% · **$0.0003/req** · p50 2.8s / **p95 24.1s**
- **Finding (kept):** **"small" ≠ "cheap" end-to-end** — the smallest model (`llama-3.1-8b`) was the
  *most* expensive per answered request (verbose → more output tokens) and the slowest (24s p95). All
  three cleared the 80% gate, so cost + latency, not correctness, are the deciding columns — which is
  the whole point of adding them.

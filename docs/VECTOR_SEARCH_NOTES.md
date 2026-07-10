# Vector search: design notes & measured tradeoffs

Why this project retrieves the way it does — and the numbers behind each choice. Everything here is
either measured in this repo (`docs/RUNS.md`, `scripts/bruteforce_scaling.py`) or a documented Vertex
behaviour we hit directly. Written to be read end-to-end by someone who wants the real mental model,
not the marketing one.

---

## 1. The one decision: a managed vector store, or a `for` loop?

Retrieval finds the k chunks closest (in embedding space) to a question. Two ways to do "closest":

- **Brute-force (exact):** score the query against *every* stored vector. O(N·dim) per query. 100% recall.
- **ANN (approximate):** search a pre-built structure (tree/graph) that skips most comparisons.
  Sub-linear, near-constant latency, **~95–99% recall** (you trade a little accuracy for speed).

A managed store (Vertex Vector Search, Pinecone, pgvector) is essentially "ANN + metadata filtering +
persistence + ops, as a service." So the decision reduces to: **at your N and query rate, is a
brute-force scan still under your latency budget?** If yes, you don't need the managed store yet.

### Where the crossover actually is (measured)

`scripts/bruteforce_scaling.py` — single-thread numpy, 384-dim vectors (our embedding size), top-10:

| N vectors | RAM | ms/query | queries/sec |
|---|---|---|---|
| 1,000 | 2 MB | 0.02 ms | 62,000 |
| 10,000 | 15 MB | 0.24 ms | 4,200 |
| 100,000 | 154 MB | 2.7 ms | 370 |
| 500,000 | 768 MB | 13.5 ms | 74 |

Linear fit (~27 ms per 1M vectors) → brute-force crosses a **10 ms** budget at **~370k vectors**,
**50 ms** at **~1.9M**, **100 ms** at **~3.7M** (one thread, one box).

**The takeaway for an interview:**
- Below ~**100k** vectors, exact search is **sub-3 ms** — an ANN index buys you *nothing* on latency and
  costs you recall + ops. Just do the scan (numpy, FAISS-flat, or pgvector without an index).
- ANN earns its keep at **hundreds of thousands to millions** of vectors, **or** under high QPS (latency
  × concurrency), **or** when you need the store's other features (filtering at scale, durability, HA).
- Our corpus is **262 vectors → 0.02 ms**. A managed ANN store is ~1000× oversized here — which is why
  Vertex's tree-AH literally *refused to build* it (see §3). Both ends of the scale say the same thing.

Caveat to state honestly: this is single-thread on one machine; real systems parallelize and hit memory
bandwidth limits, so treat the crossover as an order-of-magnitude (~10^5–10^6), not a hard line.

---

## 2. Keyword vs semantic vs hybrid — and why "hybrid" isn't a free win

Measured on the hardened 46-case set, same answerer/judge, only retrieval changing (`docs/RUNS.md`):

| Retrieval | Correctness | Best at | Fails at |
|---|---|---|---|
| **Keyword** (term overlap) + filter — Run 4 | **93%** | exact tokens: `INC-110`, `checkout-v67`, error codes | paraphrase; same-service near-duplicates |
| **Semantic** (embeddings) via Vertex — Run 5 | **83%** | symptoms/synonyms ("can't log in" ≈ "auth failure") | **exact IDs** — an embedding of `INC-110` is meaningless |
| **Hybrid** (keyword+semantic, RRF) + filter — Run 6 | **93%** | fixed the date-disambiguation cases | naive fusion let semantic noise **dilute** 2 exact-ID matches |
| **Hybrid + query routing** (ID→keyword) — Run 7 | **100%** (46/46) | routes exact IDs to lexical, symptoms to hybrid | ceiling on this set — expect ±1 case run-to-run |

**The proof that keyword ≠ semantic** (verified directly, not asserted): for "incident **INC-110**",
keyword retrieval puts INC-110 in the top-4; semantic retrieval returns INC-107/INC-109 (similar-looking
checkout incidents) and **misses INC-110 entirely**. An opaque identifier has no semantic neighbourhood.

**Why hybrid alone was a wash (Run 6 = Run 4 = 93%, different failures):** Reciprocal Rank Fusion with
equal weight gives the semantic ranking enough pull to displace keyword's exact hit for some ID queries,
even as it *fixes* the symptom/date cases. Net zero. **Hybrid's value depends entirely on the fusion.**

**The fix — query-aware routing (Run 7):** detect an exact identifier in the query (`INC-\d+`,
`service-v\d+`) → route to **keyword**; otherwise use hybrid. Route by what the query *is*, don't blend
blindly. This is the standard production pattern (lexical + vector with a router/boost), and it's what
lets you keep keyword's exact-match strength *and* semantic's paraphrase strength at once.

### RRF in one line
Reciprocal Rank Fusion merges ranked lists by summing `1/(k0 + rank)` across lists — score-free, robust,
no need to calibrate keyword scores against cosine distances. Great default; still needs weighting/routing
when one signal is decisively right (exact IDs).

---

## 3. Vertex AI Vector Search — the specifics we learned by using it

- **Index algorithms:** `tree-AH` (ScaNN — the production ANN) and `brute-force` (exact). **tree-AH
  won't build on a tiny corpus** (its tree/leaf sharding needs far more than 262 points → `FAILED_
  PRECONDITION`). Brute-force is the correct choice at small N and still supports filtering.
- **Input format:** JSONL of `{id, embedding, restricts, numeric_restricts}` in GCS — but the file must
  end `.json`/`.csv`/`.avro`. A `.jsonl` extension is rejected as "unknown format" (cost us a run).
- **Filtering** (the feature worth paying for):
  - `restricts` = categorical (namespace `service`, `allow`/`deny`) — "search only checkout chunks."
  - `numeric_restricts` = numeric (namespace `date`, `value_int`, ops `GREATER_EQUAL`/`LESS_EQUAL`) —
    enables a true **range** filter (a date window), which a keyword heuristic can't weight. This is
    what fixed our same-service-near-duplicate failures.
  - Filtering is **pre-filter** (the ANN search is constrained to the allowed set), so it stays fast.
- **Serving = a deployed index on a dedicated node.** Shard size ↔ machine type is fixed:
  `SHARD_SIZE_SMALL`→`e2-standard-2`, `SHARD_SIZE_MEDIUM`→`e2-standard-16`. Mismatch = `INVALID_ARGUMENT`.
- **Cost model:** the **endpoint node bills per hour and does not scale to zero** — the dominant cost.
  Index build (batch) and GCS storage are pennies. So the ops rule is literally "up, use, down"; leaving
  an endpoint deployed is the expensive mistake. Our whole experiment (build→deploy→eval→teardown) was
  ~15 min live, ~$0.30, torn down by script; the vectors persist in the GCS bucket.
- **Update methods:** `BATCH_UPDATE` (rebuild from GCS) vs `STREAM_UPDATE` (upsert individual points,
  higher cost, near-real-time). Batch is right for a corpus that changes in bulk.

---

## 4. Decision framework (say this in an interview)

1. **N < ~100k and low QPS?** Exact search (numpy / FAISS-flat / pgvector no-index). Simplest, exact,
   no ops. Don't reach for a managed ANN store to look modern.
2. **N in 10^5–10^7, or high QPS, or need filtered search at scale / durability / HA?** Now a managed
   ANN store (Vertex/Pinecone/pgvector+ivfflat) earns it. Accept ~95–99% recall; tune it.
3. **Whichever store, get retrieval *strategy* right first.** Exact identifiers → lexical; natural-language
   symptoms → semantic; combine with routing/weighted-hybrid, not blind fusion. The store is the
   substrate; the strategy is where correctness is won or lost — our 83→93 swing was all strategy, at
   fixed store and model.
4. **Filtering is often the real reason to adopt a store** — metadata pre-filtered ANN (service + date
   range) is hard to hand-roll well. That, not raw ANN speed, was the feature that helped us.

---

## 5. Quick Q&A

- **"When would you actually need a vector DB?"** → At ~10^5–10^6+ vectors or real QPS, where a brute-force
  scan blows the latency budget (measured: ~370k vectors ≈ 10 ms/query single-thread), or when you need
  filtered ANN / durability / HA. Below that, exact search is faster to build and 100% recall.
- **"Isn't semantic search strictly better than keyword?"** → No. Embeddings can't match opaque tokens —
  measured: pure semantic missed exact `INC-110` lookups and *dropped* our score 93→83. Keyword and
  semantic have complementary failure modes; that's why hybrid + routing exists.
- **"So just use hybrid?"** → Only if the fusion is right. Naive equal-weight RRF was a wash for us (fixed
  symptoms, broke IDs). Query-aware routing (exact-ID → lexical) is what made hybrid actually dominate.
- **"Why did tree-AH fail?"** → Too few vectors (262). ANN indexes are large-N structures; below a few
  thousand points there's nothing to approximate — use brute-force/exact.
- **"What does the managed store cost?"** → The deployed endpoint node bills hourly and doesn't scale to
  zero; that's the number to watch. Build/storage are negligible. Tear down when idle.

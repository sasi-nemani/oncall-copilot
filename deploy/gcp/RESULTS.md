# Model bake-off — every run, logged and explainable

This is the honest ledger for the "which model, how good?" experiments run against the
**self-hosted GCP box** (and, once its key works, **OpenRouter** for big models via API).
Every row is a real run: what model, what config, what it scored, and what we did. Raw
per-run summaries are preserved under [`results/`](./results/).

## How to read this

- **The suite:** the same 46-case eval (`evals/dataset.jsonl`) every time — incident questions
  covering correctness, tool-choice, safety (incl. prompt-injection + multi-turn). Same harness,
  so numbers are comparable *across models*.
- **The three sub-scores** (a case passes only if **all three** pass, which is why the headline
  pass rate is lower than any single sub-score):
  - **correctness** — did it give a grounded, cited, accurate diagnosis (judged by an LLM)?
  - **tool_choice** — did it call the right read-only tool for the question?
  - **safety** — did it refuse unsafe actions + injection/false-authority traps?
- **The judge:** an LLM grades correctness. Unless noted, the judge is **qwen2.5:7b**, held
  constant so a change in the *answerer* is the only moving part.
- **Runs ×2:** every config is run twice. Two close numbers = trustworthy; a big swing = noisy.
- **Hardware:** one NVIDIA L4 (24GB) on GCP, Spot, models served by Ollama (4-bit quantized).
  See [README.md](./README.md) for the deploy. Baked into image `oncall-models-v2`.

## Results

| # | Answerer | Size | Backend | Judge | Run 1 pass | Run 2 pass | correctness | tool_choice | safety | Errors |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | **mistral-nemo** | 12B | self-hosted L4 | qwen2.5:7b | 33% | 37% | 43% / 43% | 85% / 76% | 96% / 100% | 0 |
| 2 | **qwen2.5:14b** | 14B | self-hosted L4 | qwen2.5:7b | 50% | 50% | 54% / 52% | 91% / 93% | 93% / 96% | 0 |
| 3 | **qwen2.5:32b** | 32B | self-hosted L4 | qwen2.5:7b | _running_ | _running_ | — | — | — | — |
| 4 | qwen2.5:14b + **hybrid retrieval** | 14B | self-hosted L4 | qwen2.5:7b | _pending_ | _pending_ | — | — | — | — |
| 5 | qwen2.5:14b, **different-family judge** | 14B | self-hosted L4 | mistral-small | _pending_ | _pending_ | — | — | — | — |
| 6 | **llama-3.3-70b** | 70B | OpenRouter (API) | **deepseek-chat** (independent) | **57%** | **61%** | 57% / 63% | 96% / 96% | 96% / 98% | 0 |
| 7 | **claude-sonnet-4** (frontier) | — | OpenRouter (API) | **deepseek-chat** (independent) | **83%** ✅ | **85%** ✅ | 87% / 87% | 96% / 98% | 100% / 100% | 0 |

**Headline finding:** pass rate tracks model capability almost monotonically on the same suite —
**nemo-12B ~35% → qwen-14B 50% → llama-70B ~59% → claude-sonnet-4 ~84%.** The **80% ship gate is
passable**, and it takes a **frontier model** to clear it (row 7, verified with an *independent*
judge — not self-graded). The axis that moves is **correctness** (43% → 87%); tool-choice and
safety saturate early (~90–100% even on small models), because they need judgement, not raw
reasoning. The cheapest way to buy accuracy was **a better model via API**, not a bigger GPU —
self-hosting taught the deployment, the API bought the correctness. The self-hostable/open models
are *safe and sensible but not yet accurate enough to ship*; that gap is the whole story.

## What we did, and what each run tells us

### 1 · mistral-nemo (12B) — the baseline
First self-hosted run, chosen for reliable tool-calling on a 12B that fits the L4. **~35% pass,
stable.** Reads: safe (~98%) and picks tools well (~80%), but weak on answer **correctness (43%)**
— a small model is *sensible and cautious* but not *accurate*. This is the run the free API tiers
could never finish (they 429'd at ~4/46); self-hosting removed the rate limit → 0 errors.

### 2 · qwen2.5:14b (14B) — does a bigger model help? Yes.
Same everything, bigger answerer. **50% pass, rock-stable both runs.** correctness jumped
**43% → ~53%** and tool_choice **~80% → ~92%**. Clean evidence that model capability is the lever
for correctness. **Caveat:** answerer and judge are both Qwen — same-family grading can *inflate*
correctness via style affinity; run #5 checks this with a different-family judge.

### 3 · qwen2.5:32b (32B) — the size ceiling on one L4
Biggest model that fits a 24GB L4. Judge held at qwen2.5:7b for a clean size comparison — but
32B (~20GB) + the 7B judge (~5GB) exceed 24GB, so Ollama swaps models per case (slow, valid).
_Numbers filled in when the run completes._

### 4 · hybrid retrieval (model-independent lever)
Switch `RETRIEVAL_MODE=keyword → hybrid` (local embeddings, sentence-transformers). Tests whether
better *evidence retrieval* lifts correctness without a bigger model — "fix the instrument, not the
model." _Pending._

### 5 · different-family judge (honesty check)
qwen2.5:14b answerer graded by **mistral-small** (different lineage, co-resident on the L4, no
swap). If correctness holds vs run #2, the Qwen-on-Qwen number was honest; if it drops, some of
run #2's correctness was family affinity. _Pending._

### 6 · llama-3.3-70b via OpenRouter (big model, API)
A 70B is too big for one L4, but cheap on OpenRouter. **~59% pass (57% / 61%), correctness ~60%,
tools 96%, safety 97% — the best result in the bake-off, and the most trustworthy**: it's the only
run with a genuinely **independent judge** (Meta answerer graded by a DeepSeek judge), so unlike the
same-family self-hosted rows it can't be inflated by style affinity. Both runs 0 errors, run via
API in parallel with the GPU work (no hardware needed). Two runs cost well under $1.

_Gotcha logged: the first attempt scored 0/46 — not rate limits, but `.env` pins every role to
Gemini (dead free quota), and a too-aggressive `load_dotenv(override=True)` let those beat the
command-line `openrouter` overrides. Fixed so `.env` wins for **API keys only**, not role vars._

## Methodology & honest caveats

- **Judge independence:** correctness is LLM-judged, so the judge is a variable, not ground truth.
  We hold it constant to compare answerers, and run #5 deliberately changes it to test for bias.
- **Quantization:** all self-hosted models run 4-bit (Ollama default) — a small quality haircut vs
  full precision, but full precision doesn't fit a 14B+ model on a 24GB L4 anyway.
- **Nondeterminism:** answerer + judge aren't fully deterministic; hence ×2 runs and reporting the
  spread, not a single number.
- **The goal isn't to beat 80% self-hosted.** 80% is the *product* ship gate (with a frontier
  model). This bake-off measures the *self-hostable capability gap* and proves the harness is
  portable across backends — that's the finding, not the pass rate.

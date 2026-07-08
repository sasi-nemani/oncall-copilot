# Self-hosted models on GCP (Terraform)

Spin up **one L4 GPU VM** that serves two open models behind an OpenAI-compatible endpoint,
run the full eval against them (no rate limits), then tear it down. Everything is Infrastructure
as Code so `up` and `down` are each one command.

**Why this exists:** free hosted API tiers (Gemini/OpenRouter `:free`) rate-limit hard — the
46-case eval can't finish; it 429s partway through at any pacing. A model *you* host has no
per-minute cap, so the eval runs start-to-finish. The trade: the GPU costs money **while it's
up**. So the rule is **apply → run → destroy**. Don't leave it running.

```
answerer (tool-capable)   Mistral-7B   via Ollama   ─┐
                                                       ├─  :11434/v1  (OpenAI-compatible)
judge (different, smaller) Qwen2.5-3B   via Ollama   ─┘
```

Both models fit together on a single 24GB L4 (Nemo ~7GB + Qwen ~4.7GB quantized).

### Measured result (2026-07-08) — the run the free tier couldn't finish

Two back-to-back runs, `mistral-nemo` answering + `qwen2.5:7b` judging, **0 errors** (the free
hosted tiers 429'd out after ~4 of 46 cases; self-hosted finished all 46, twice):

| Run | Pass | correctness | tool_choice | safety |
|---|---|---|---|---|
| 1 | 33% | 43% | 85% | 96% |
| 2 | 37% | 43% | 76% | 100% |

Stable at ~35% (±2). A 12B open model is *safe* (~98%) and picks the right *tool* (~80%) but is
weaker on answer *correctness* (43%) than a frontier model — the capability gap, measured cleanly.

#### Why deploy on GCP at all?

Two reasons, one forced and one chosen. **Forced:** the free hosted tiers rate-limit hard — a
46-case agent eval is ~180 model calls in a burst, and Gemini/OpenRouter `:free` returned 429
("too many requests") partway through *at any pacing* (one paced attempt scored only 4/46 before
the rest errored). To get a *complete* number I needed a backend with no per-minute cap, which
means hosting the model myself. **Chosen:** doing it as Terraform on a GPU VM is the SRE/FDE skill
I want on the table — provision accelerator infra as code, serve an open model, run a batch job,
tear it down. The `apply → run → destroy` loop is also the honest cost story: no rate limit, but
you pay for the GPU while it's up, so you don't leave it up.

#### Why these two models?

- **Answerer = Mistral-Nemo 12B (not Mistral-7B).** The answerer *drives the agent loop* — it must
  emit clean `tool_calls`, and a single malformed call breaks the whole trajectory. Nemo was built
  with function-calling as a first-class feature and is markedly more reliable at it than 7B, while
  still fitting a 24GB L4 (~7GB quantized). Tool-calling reliability was the hard requirement; Nemo
  meets it (verified with a smoke test before the run).
- **Judge = Qwen2.5-7B (a *different* family).** A judge's job is faithful rubric-following, and it
  must be **independent** — grading with the *same* model that answered reintroduces self-preference
  bias (a model likes its own style). Qwen is a strong small instruction-follower from a completely
  different lineage than Mistral, so the grade isn't the answerer marking its own homework. ~4.7GB,
  fits alongside Nemo on the one GPU.

#### Why each sub-score came out where it did

The pass rate combines three independent suites (a case passes only if **all three** pass), so the
split is more informative than the headline:

- **Safety ~96–100% — highest, and expected.** Safety here is mostly *refusal*: don't act on
  "restart the service", "delete the logs", or the injected "ignore your instructions" lines. That's
  a **structural** property of this system (the tools are read-only by construction) reinforced by
  the prompt — so even a smaller model clears it, because it doesn't need deep reasoning to *not do*
  a destructive thing. This is the one dimension where model size barely matters.
- **Tool-choice ~76–85% — good, some variance.** Picking the right read-only tool for a question
  ("check logs" → `search_logs`) is a **classification** task the 12B model handles well most of the
  time. It's the noisiest of the three across runs because a borderline question can map to two
  plausible tools; that's genuine model nondeterminism, not a broken suite.
- **Correctness 43% — lowest, and the real capability gap.** Correctness is the hard one: read the
  right evidence, reason over it, and produce a *grounded, cited, accurate* diagnosis that a
  different-model judge accepts. This needs the reasoning depth a 12B open model simply has less of
  than a frontier model (Haiku scored ~90% on the same suite). It was **identical (43%) across both
  runs** — so it's a stable property of the model, not luck. The takeaway isn't "the model is bad";
  it's that a self-hostable model is *safe and sensible* but not yet *frontier-accurate*, and the
  harness surfaces exactly that instead of hiding it behind one blended number.

---

## Cost — read this first

| Item | Rough cost |
|---|---|
| `g2-standard-8` (1× L4), on-demand | **~$0.70–0.85 / hour** |
| A full eval run (boot + pulls + 46 cases) | ~30–45 min ⇒ **well under $1** |
| Leaving it running 24/7 | ~**$500 / month** ⇒ your $100 credit lasts ~6 days |

Your $100 free credit is plenty for **dozens of apply→run→destroy cycles**. It is *not* for
leaving a GPU idle. `terraform destroy` is the cost control.

---

## Step 0 — the day-one blocker: GPU quota

**New GCP projects ship with GPU quota = 0.** Check and request an increase *before* anything
else, or `apply` will fail with a quota error.

```bash
# after gcloud auth + project set (Step 1):
gcloud compute regions describe us-central1 \
  --format="table(quotas.filter('metric:NVIDIA_L4_GPUS').list())"
```

If the limit is `0`, request an increase (Console → **IAM & Admin → Quotas** → filter
"NVIDIA L4 GPUs" → region `us-central1` → Edit → request `1`). On free-trial billing this
can take minutes to a day, and some trial accounts must **upgrade to a paid billing account**
(you keep the $100 credit) before GPU quota is granted. Nothing below works until this reads ≥ 1.

---

## Step 1 — account, project, billing  *(you run these — they need your credentials)*

```bash
gcloud auth login
gcloud projects create oncall-copilot-<unique> --name="On-Call Copilot"
gcloud config set project oncall-copilot-<unique>
gcloud billing accounts list                       # copy your BILLING_ACCOUNT_ID
gcloud billing projects link oncall-copilot-<unique> --billing-account=BILLING_ACCOUNT_ID
gcloud auth application-default login               # lets Terraform authenticate
```

**Set a budget alarm** (belt-and-braces, so a forgotten VM emails you):
```bash
gcloud billing budgets create --billing-account=BILLING_ACCOUNT_ID \
  --display-name="oncall-cap" --budget-amount=25USD \
  --threshold-rule=percent=0.5 --threshold-rule=percent=0.9
```

## Step 2 — configure Terraform

```bash
cd deploy/gcp
terraform init
```

Create `terraform.tfvars` (gitignored — never commit it):
```hcl
project_id   = "oncall-copilot-<unique>"
allowed_cidr = "YOUR.PUBLIC.IP.HERE/32"   # `curl -s ifconfig.me` then add /32
# region/zone/models default sensibly; override here if your quota is elsewhere.
```
The endpoint is locked to `allowed_cidr` — **your IP only**. Never widen it to `0.0.0.0/0`;
an open Ollama is an open, abusable LLM.

## Step 3 — apply

```bash
terraform plan     # read what it will create (1 VM, 1 firewall, 1 SA, API enablement)
terraform apply    # ~2 min to create; the VM then pulls models for a few more minutes
```

## Step 4 — wait for the models to be ready

`apply` returns before the models finish downloading. Note the endpoint answers `200` with an
**empty** list as soon as Ollama boots — wait for the actual model names to appear:
```bash
IP=$(terraform output -raw instance_ip)
until curl -sf "http://$IP:11434/v1/models" | grep -q mistral-nemo; do echo "pulling..."; sleep 15; done
curl -s "http://$IP:11434/v1/models"     # should list mistral-nemo + qwen2.5:7b
```
Watch progress on the serial console (no SSH needed): `gcloud compute instances
get-serial-port-output oncall-model --zone us-central1-a | grep oncall`.

> **Note:** the first inference call is slow — cold-start load of a 12B model into VRAM takes
> 30–60s. Warm it once before timing anything:
> `curl http://$IP:11434/api/generate -d '{"model":"mistral-nemo","prompt":"hi","stream":false}'`.

**Verify tool-calling works** (the investigator needs it — do this before trusting a full run):
```bash
IP=$(terraform output -raw instance_ip)
curl -s "http://$IP:11434/v1/chat/completions" -H 'Content-Type: application/json' -d '{
  "model":"mistral","messages":[{"role":"user","content":"call the get_weather tool for Paris"}],
  "tools":[{"type":"function","function":{"name":"get_weather","description":"weather",
    "parameters":{"type":"object","properties":{"city":{"type":"string"}},"required":["city"]}}}]
}' | python3 -c "import sys,json;print(json.load(sys.stdin)['choices'][0]['message'].get('tool_calls'))"
```
If that prints a tool call, the answerer is good. If it's `None`/flaky, switch `answerer_model`
to `qwen2.5` or `llama3.1` (strong Ollama tool support) in `terraform.tfvars` and re-apply.

## Step 5 — run the full eval against your box

```bash
cd ../..                                    # repo root
eval "$(cd deploy/gcp && terraform output -raw run_eval_hint | sed 's/ ; /\n/')"
python3 -m evals.run_evals                   # use python3 if `python` isn't on your PATH
```
This is the run the free tier couldn't finish — all 46 cases, on models you host.
Consider running it **twice** — the answerer/judge aren't fully deterministic, and two runs
tell you whether the number is stable (mine landed 33% then 37% — trustworthy) or noisy.

## Step 6 — DESTROY (do not skip)

```bash
cd deploy/gcp
terraform destroy
```
Confirm it's gone: `gcloud compute instances list` should show nothing.

---

## Why Ollama and not vLLM?

Both are model servers (load a model, answer requests over an OpenAI-compatible API). They
optimise for different jobs:

| | **Ollama** (used here) | **vLLM** |
|---|---|---|
| Built for | Local/dev, single box, convenience | Production serving at scale |
| Many concurrent requests | Roughly one-at-a-time → lower throughput | Continuous batching → far higher throughput |
| Multiple models on one GPU | Easy — loads/unloads on demand | One model per process; two = two processes splitting the GPU |
| Setup | One install script + `ollama pull` | Python deps, CUDA/torch matching, HuggingFace downloads |
| Model source | Own library, no login | HuggingFace — Mistral weights are license-gated (token) |
| Default precision | 4-bit quantised (fits more, slight quality hit) | Fuller precision (higher fidelity, needs much more VRAM) |

**Why Ollama fits this project:** the eval runs **one case at a time** (`EVAL_WORKERS=1`), so
vLLM's concurrency superpower would be wasted — nothing to batch. We also need **two different
models** (answerer + judge) co-resident on **one** L4, which Ollama does trivially and vLLM does
not. And Ollama sidesteps the HuggingFace gated-weight friction for Mistral. **When you'd switch:**
the day this is a real service with many engineers querying at once — then throughput is
everything and vLLM serves that crowd on far less hardware. Knowing *which to reach for when* is
the point; for a single-user batch job, Ollama wins.

**One honest nuance:** "vLLM is higher quality because it runs full precision" is true in
general but **not on a 24GB L4** — a 14B model at full precision needs ~28GB, so *both* servers
must run it quantised here. On this box the real quality lever is a **bigger/better model**, not
the server software.

## Other notes & honest caveats

- **One VM, not Cloud Run:** serving an open LLM needs a persistent GPU (no scale-to-zero), so
  a managed autoscaler adds moving parts for no benefit on a batch job. Scale-to-zero is the
  right pattern for the *app*, not the model box.
- **State files** (`*.tfstate`) can contain resource detail — gitignored here; keep them local.
- **If `apply` fails on quota**, that's Step 0 — the L4 limit is still 0. Nothing to debug in TF.

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

Both models are ~4–5GB quantized and fit together on a single 24GB L4.

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

`apply` returns before the models finish downloading. Poll the endpoint:
```bash
IP=$(terraform output -raw instance_ip)
# 200 + a model list means it's serving:
until curl -sf "http://$IP:11434/v1/models" >/dev/null; do echo "warming up..."; sleep 15; done
curl -s "http://$IP:11434/v1/models"     # should list mistral + qwen2.5:3b
```
Watch setup logs directly if impatient: `terraform output -raw ssh_command` → then on the box
`sudo journalctl -u ollama -f` and `tail -f /var/log/oncall-setup.log`.

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
# or just copy the exported line from:  terraform output -raw run_eval_hint
python -m evals.run_evals
```
This is the run the free tier couldn't finish — all 46 cases, on models you host.

## Step 6 — DESTROY (do not skip)

```bash
cd deploy/gcp
terraform destroy
```
Confirm it's gone: `gcloud compute instances list` should show nothing.

---

## Notes & honest caveats

- **Ollama, not vLLM (on purpose):** Ollama serves multiple models on one GPU with the least
  fuss and dodges HuggingFace gated-weight friction. vLLM gives higher throughput and is the
  production upgrade — irrelevant for a 46-case batch. Documented, not hidden.
- **One VM, not Cloud Run:** serving an open LLM needs a persistent GPU (no scale-to-zero), so
  a managed autoscaler adds moving parts for no benefit on a batch job. Scale-to-zero is the
  right pattern for the *app*, not the model box.
- **State files** (`*.tfstate`) can contain resource detail — gitignored here; keep them local.
- **If `apply` fails on quota**, that's Step 0 — the L4 limit is still 0. Nothing to debug in TF.

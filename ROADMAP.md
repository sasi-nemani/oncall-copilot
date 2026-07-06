# Roadmap — v2

This is the v2 direction, filtered for what's genuinely useful to anyone running this — not interview optics. Items graduate from here into [`IMPROVEMENTS.md`](./IMPROVEMENTS.md) when they land, with the real numbers they produced.

## P0 — do next

- [ ] **Real corpus + ingestion** — replace the 6 mock runbooks with a real docs corpus and an ingestion script, so retrieval quality is measured on something that resembles production.
- [ ] **Evals in CI** (≥50 cases, 4 suites) — run the eval on every push, re-baseline the scorecard tables, split into correctness / tool-choice / safety / refusal suites.
- [ ] **Prompt-injection safety suite** — adversarial cases where retrieved docs or tool outputs try to steer the agent (exfiltrate, fake approval, claim actions were taken).
- [x] **Richer ops tools** (`get_alerts`, `get_incident_timeline`) — done 2026-07-06. Two new read-only tools + 4 eval cases (36→40); see [`IMPROVEMENTS.md`](./IMPROVEMENTS.md).
- [ ] **Per-query cost/latency telemetry** — record tokens, cost, and wall-clock per run in the JSONL trace, so "which model?" decisions include price, not just pass rate.

## P1 — after that

- [ ] Pluggable vector-index interface (swap the in-memory index for FAISS/pgvector behind one seam).
- [ ] Deploy-as-code: Dockerfile + optional Cloud Run/Terraform, so "runs from a clean checkout" extends to "deploys from a clean checkout".
- [ ] Scripted demo scenario with a seeded tool-timeout, to show error-as-result recovery live instead of describing it.
- [ ] Citation panel + trace links in the visualizer.
- [ ] Cost columns on the model comparison table.

## P2 — design-only (deliberately not built)

These are worth designing on paper but building them here would be scope theater:

- Pluggable serving backend (vLLM/self-hosted behind the provider interface).
- A dedicated guardrails layer (policy engine in front of every model call, not just the answer check).
- Propose-to-ticket: the "needs human approval" proposals become real tickets in an issue tracker.
- SLO definitions + a chaos drill that degrades a dependency and grades the assistant's diagnosis.

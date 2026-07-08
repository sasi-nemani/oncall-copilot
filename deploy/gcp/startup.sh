#!/usr/bin/env bash
# Runs once on first boot (GCP startup-script). Turns a bare CUDA VM into an
# OpenAI-compatible model server. Terraform fills in ${answerer_model} / ${judge_model}.
# Watch progress after boot with:  sudo journalctl -u ollama -f   (and cat /var/log/oncall-setup.log)
set -euxo pipefail
exec > >(tee -a /var/log/oncall-setup.log) 2>&1

echo "[oncall] waiting for the NVIDIA driver (installed by the install-nvidia-driver metadata flag)..."
for i in $(seq 1 60); do
  if command -v nvidia-smi >/dev/null && nvidia-smi >/dev/null 2>&1; then
    echo "[oncall] GPU is up:"; nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
    break
  fi
  echo "[oncall] driver not ready yet ($i/60), sleeping 10s"; sleep 10
done

echo "[oncall] installing Ollama..."
curl -fsSL https://ollama.com/install.sh | sh

# Bind Ollama to all interfaces so the firewall-restricted public IP can reach it.
# (Default binds to localhost only — that would make the endpoint unreachable.)
mkdir -p /etc/systemd/system/ollama.service.d
cat > /etc/systemd/system/ollama.service.d/override.conf <<'EOF'
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
EOF
systemctl daemon-reload
systemctl enable --now ollama
systemctl restart ollama

# CRITICAL: wait for the Ollama API to actually answer before pulling. `systemctl restart`
# returns before the server is listening, so pulling immediately races the socket and fails
# silently — which is exactly what bit the first deploy. Poll /api/tags until it's up.
echo "[oncall] waiting for the Ollama API to accept connections..."
for i in $(seq 1 30); do
  if curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
    echo "[oncall] Ollama API is up"; break
  fi
  echo "[oncall] API not ready ($i/30), sleeping 5s"; sleep 5
done

echo "[oncall] pulling models (this is the slow part — several GB each)..."
for m in "${answerer_model}" "${judge_model}"; do
  for attempt in 1 2 3 4 5; do
    if ollama pull "$m"; then echo "[oncall] pulled $m"; break; fi
    echo "[oncall] pull $m failed (attempt $attempt), retrying in 15s"; sleep 15
  done
  # Verify it registered; if not, make it visible in the log rather than exiting 0 silently.
  ollama list | grep -q "$m" && echo "[oncall] confirmed $m" || echo "[oncall] WARNING: $m NOT present after retries"
done

# A readiness marker the client can poll for (see README).
echo "[oncall] warming models..."
ollama run "${answerer_model}" "reply with the single word: ready" >/dev/null 2>&1 || true
touch /var/run/oncall-ready
echo "[oncall] SETUP COMPLETE — models served on :11434/v1"

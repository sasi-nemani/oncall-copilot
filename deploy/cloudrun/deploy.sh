#!/usr/bin/env bash
# One-command deploy of the On-Call Copilot app to Cloud Run.
#
#   ./deploy.sh                 # build image + apply infra + wire the secret
#   terraform destroy           # tear it all down (scale-to-zero means idle cost is ~$0 anyway)
#
# Order matters and is the whole point of the secret hygiene:
#   1. create the prerequisite infra (Artifact Registry, Secret container, runtime SA) via Terraform
#   2. add the API KEY VALUE with gcloud (never through Terraform -> never in tfstate/repo)
#   3. build+push the image with Cloud Build (no local Docker needed)
#   4. apply the full stack (Cloud Run now finds both the image and the secret version)
set -euo pipefail
cd "$(dirname "$0")"

PROJECT="${GCP_PROJECT:-linkedinpost-agentsalltheway}"
REGION="${GCP_REGION:-us-central1}"
TAG="$(git -C ../.. rev-parse --short HEAD 2>/dev/null || echo latest)"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/oncall-copilot/app:${TAG}"

echo "== project=$PROJECT region=$REGION image=$IMAGE =="

# The key comes from the repo-root .env (gitignored) — same source the app uses locally.
KEY="$(python3 -c 'from dotenv import dotenv_values; print(dotenv_values("../../.env").get("OPENROUTER_API_KEY",""))')"
[ -n "$KEY" ] || { echo "ERROR: OPENROUTER_API_KEY not found in ../../.env"; exit 1; }

terraform init -input=false

echo "== [1/3] create prerequisites (AR repo, secret container, runtime SA) =="
terraform apply -input=false -auto-approve -var="project_id=$PROJECT" -var="region=$REGION" -var="image=$IMAGE" \
  -target=google_artifact_registry_repository.app \
  -target=google_secret_manager_secret.llm_key \
  -target=google_service_account.run \
  -target=google_secret_manager_secret_iam_member.run_reads_key

echo "== [2/3] add the API key as a secret version (gcloud, not Terraform) =="
printf '%s' "$KEY" | gcloud secrets versions add oncall-openrouter-key --data-file=- --project="$PROJECT" >/dev/null
echo "   secret version added."

echo "== build+push image via Cloud Build =="
gcloud builds submit ../.. --tag "$IMAGE" --project="$PROJECT" --quiet

echo "== [3/3] deploy the full stack =="
terraform apply -input=false -auto-approve -var="project_id=$PROJECT" -var="region=$REGION" -var="image=$IMAGE"

echo
echo "== DONE =="
terraform output

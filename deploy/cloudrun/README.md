# Deploy the app to Cloud Run (as code)

Scale-to-zero web service for the On-Call Copilot app. Idle cost ≈ $0 (0 instances until a request).

## One command up
```bash
export GCP_PROJECT=<your-project>       # defaults to linkedinpost-agentsalltheway
./deploy.sh
```
`deploy.sh` creates the infra (Terraform), adds the LLM API key to **Secret Manager** with `gcloud`
(so the key never enters Terraform state or the repo), builds+pushes the image with **Cloud Build**,
then deploys the **Cloud Run** service under a **least-privilege runtime service account** whose only
permission is reading that one secret.

## One command down
```bash
terraform destroy -var="project_id=$GCP_PROJECT" -var="image=unused"
```

## Reach it
The service is **authenticated-only** by default (an open URL that makes paid LLM calls is abusable):
```bash
curl -H "Authorization: Bearer $(gcloud auth print-identity-token)" "$(terraform output -raw service_url)"
```
For a public demo URL, set `-var="allow_unauthenticated=true"` — knowingly.

## What's in here
| File | Role |
|---|---|
| `main.tf` | Artifact Registry · Secret Manager (container only) · least-priv runtime SA · Cloud Run v2 (min=0) · invoker IAM |
| `variables.tf` | project / region / image / model / scaling / public-toggle |
| `outputs.tf` | service URL, runtime SA, authenticated-curl hint |
| `deploy.sh` | staged apply → secret via gcloud → Cloud Build → full apply |

# Deploy the app to Cloud Run (as code)

Scale-to-zero web service for the On-Call Copilot app. Idle cost ≈ $0 (0 instances until a request).

## First-time project setup (once per project)

`gcloud builds submit` runs as the project's default **compute** service account. On a locked-down
project that SA has no roles, so Cloud Build 403s reading the uploaded source. Grant it the build role
once:
```bash
PROJ=<your-project>; NUM=$(gcloud projects describe $PROJ --format='value(projectNumber)')
gcloud projects add-iam-policy-binding $PROJ \
  --member="serviceAccount:${NUM}-compute@developer.gserviceaccount.com" \
  --role="roles/cloudbuild.builds.builder"
```
(Or skip Cloud Build and build locally — see "Build locally without Cloud Build" below.)

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

## Build locally without Cloud Build

If you'd rather not use Cloud Build, build + push the image yourself (Cloud Run runs amd64):
```bash
IMAGE=us-central1-docker.pkg.dev/$GCP_PROJECT/oncall-copilot/app:local
gcloud auth configure-docker us-central1-docker.pkg.dev -q
docker build --platform linux/amd64 -t $IMAGE ../..
docker push $IMAGE
terraform apply -var="project_id=$GCP_PROJECT" -var="image=$IMAGE"
```

## What's in here
| File | Role |
|---|---|
| `main.tf` | Artifact Registry · Secret Manager (container only) · least-priv runtime SA · Cloud Run v2 (min=0) · invoker IAM |
| `variables.tf` | project / region / image / model / scaling / public-toggle |
| `outputs.tf` | service URL, runtime SA, authenticated-curl hint |
| `deploy.sh` | staged apply → secret via gcloud → Cloud Build → full apply |

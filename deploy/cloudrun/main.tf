# On-Call Copilot — the APP, as code, on Cloud Run.
#
# Scale-to-zero is the RIGHT pattern for a request-driven web app (the opposite of the model box in
# deploy/gcp/, which needs an always-on GPU). Idle => 0 instances => ~$0. A request spins one up.
#
# Shape:
#   Artifact Registry  <- the container image (built by deploy.sh via Cloud Build)
#   Cloud Run service  -> runs the image, min=0 (scale to zero), under a dedicated runtime SA
#   Secret Manager     -> holds the LLM API key; the runtime SA is the ONLY thing that can read it,
#                         and the key value is added out-of-band (gcloud), so it never enters
#                         Terraform state or the repo. That's the least-privilege + secret-hygiene point.
#
# One command up:  ./deploy.sh          One command down:  terraform destroy

terraform {
  required_version = ">= 1.5"
  required_providers {
    google = { source = "hashicorp/google", version = "~> 5.0" }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# APIs the deploy needs. disable_on_destroy=false so `destroy` doesn't turn off services other
# things in the project might use.
resource "google_project_service" "apis" {
  for_each           = toset(["run.googleapis.com", "artifactregistry.googleapis.com", "secretmanager.googleapis.com", "cloudbuild.googleapis.com"])
  service            = each.value
  disable_on_destroy = false
}

# A Docker repository to hold the app image (region-local, so pulls are fast at cold start).
resource "google_artifact_registry_repository" "app" {
  location      = var.region
  repository_id = "oncall-copilot"
  format        = "DOCKER"
  description   = "On-Call Copilot app images"
  depends_on    = [google_project_service.apis]
}

# Dedicated runtime identity for the service — NOT the default compute SA (which is over-privileged).
# We grant it exactly one thing below: read the one secret it needs. Nothing else in the project.
resource "google_service_account" "run" {
  account_id   = "oncall-copilot-run"
  display_name = "On-Call Copilot Cloud Run runtime (least privilege)"
}

# The secret CONTAINER only. The value (a version) is added by deploy.sh with gcloud, so the key
# never lands in Terraform state or version control.
resource "google_secret_manager_secret" "llm_key" {
  secret_id = "oncall-openrouter-key"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

# Least privilege: the runtime SA may ACCESS this one secret — and only this one.
resource "google_secret_manager_secret_iam_member" "run_reads_key" {
  secret_id = google_secret_manager_secret.llm_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.run.email}"
}

resource "google_cloud_run_v2_service" "app" {
  name     = "oncall-copilot"
  location = var.region
  # Only allow external ingress; internal traffic rules could tighten this further.
  ingress = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.run.email
    scaling {
      min_instance_count = 0 # scale to zero — no cost when idle
      max_instance_count = var.max_instances
    }
    containers {
      image = var.image
      ports { container_port = 8080 } # Dockerfile EXPOSE / Cloud Run $PORT
      resources {
        limits = { cpu = "1", memory = "512Mi" }
      }
      # Non-secret config as plain env.
      env {
        name  = "PROVIDER"
        value = "openrouter"
      }
      env {
        name  = "OPENROUTER_MODEL"
        value = var.answerer_model
      }
      env {
        name  = "VIZ_HOST"
        value = "0.0.0.0"
      }
      # The API key comes from Secret Manager, not a plain env value — never baked into the image
      # or visible in the service config.
      env {
        name = "OPENROUTER_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.llm_key.secret_id
            version = "latest"
          }
        }
      }
    }
  }
  depends_on = [google_secret_manager_secret_iam_member.run_reads_key]
}

# Who can invoke the service. Default = authenticated only (safer: an open URL that makes paid LLM
# calls is abusable). Flip var.allow_unauthenticated=true for a public demo URL — knowingly.
resource "google_cloud_run_v2_service_iam_member" "invoker" {
  count    = var.allow_unauthenticated ? 1 : 0
  name     = google_cloud_run_v2_service.app.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}

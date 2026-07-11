# Knobs for the Cloud Run deploy. Nothing secret belongs here (the API key is added via gcloud in
# deploy.sh and lives only in Secret Manager). Override in terraform.tfvars (gitignored) or with -var.

variable "project_id" {
  type        = string
  description = "GCP project id to deploy into."
}

variable "region" {
  type        = string
  default     = "us-central1"
  description = "Cloud Run + Artifact Registry region."
}

variable "image" {
  type        = string
  description = "Full Artifact Registry image ref to deploy (set by deploy.sh after the build)."
}

variable "answerer_model" {
  type        = string
  default     = "meta-llama/llama-3.3-70b-instruct"
  description = "OpenRouter model the app answers with (needs tool support for the agent)."
}

variable "max_instances" {
  type        = number
  default     = 2
  description = "Upper bound on autoscaling. min is fixed at 0 (scale to zero)."
}

variable "allow_unauthenticated" {
  type        = bool
  default     = false
  description = "true = public URL (anyone can invoke — abusable, since requests make paid LLM calls). Default authenticated-only."
}

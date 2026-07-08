# All knobs live here so main.tf stays declarative. Override in terraform.tfvars
# (gitignored) or with -var. Nothing secret belongs in this file.

variable "project_id" {
  type        = string
  description = "GCP project id to deploy into (create it first — see README)."
}

variable "region" {
  type        = string
  default     = "us-central1"
  description = "L4 GPUs are widely available in us-central1; change if your quota is elsewhere."
}

variable "zone" {
  type        = string
  default     = "us-central1-a"
  description = "Zone within the region. Must have L4 stock + your accelerator quota."
}

variable "machine_type" {
  type        = string
  default     = "g2-standard-8"
  description = "G2 = the L4 machine family. g2-standard-8 = 1x L4 (24GB), 8 vCPU, 32GB RAM."
}

variable "allowed_cidr" {
  type        = string
  description = "YOUR public IP as a /32 (e.g. 203.0.113.7/32). Locks the model endpoint to you — never 0.0.0.0/0."
}

variable "answerer_model" {
  type        = string
  default     = "mistral"
  description = "Ollama model the agent/investigator uses. Must support tool calling (mistral, qwen2.5, llama3.1)."
}

variable "judge_model" {
  type        = string
  default     = "qwen2.5:3b"
  description = "A DIFFERENT, smaller model for the eval judge — independence (no self-grading). No tools needed."
}

variable "models_image" {
  type        = string
  default     = ""
  description = <<-EOT
    Optional: a custom image with the models baked in, so deploys DON'T re-download them.
    Empty = boot the stock Deep Learning image and pull models on first boot.
    To create one after models are pulled on a running box:
      gcloud compute images create oncall-models-v1 \
        --source-disk=oncall-model --source-disk-zone=<zone> --force --project=<project>
    then set models_image = "projects/<project>/global/images/oncall-models-v1" here.
    Images are global, so this survives destroy/preemption AND works in any zone (unlike a
    zonal persistent disk, which matters because L4 stockouts force us to change zones).
  EOT
}

variable "use_spot" {
  type        = bool
  default     = false
  description = "Use a Spot (preemptible) L4 — cheaper and often available when on-demand is stocked out, but can be reclaimed mid-run. Fine for a short batch eval; retry if preempted."
}

variable "boot_disk_gb" {
  type        = number
  default     = 100
  description = "Room for the CUDA image + both model weights (~8GB) + headroom."
}

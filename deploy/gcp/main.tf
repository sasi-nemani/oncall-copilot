# On-Call Copilot — self-hosted model box on GCP.
#
# One L4 GPU VM running Ollama, serving two open models behind an OpenAI-compatible
# endpoint (:11434/v1): a tool-capable answerer (Mistral) + a smaller judge (Qwen).
# The point: no rate limits, so the full 46-case eval runs start-to-finish — which the
# free hosted tiers can't do. The box costs money while it's up, so the workflow is
# strictly:  terraform apply  ->  run the eval  ->  terraform destroy.
#
# Deliberately simple: a single VM, not Cloud Run / a managed endpoint. Serving an open
# LLM needs a persistent GPU (no scale-to-zero), so a managed autoscaler buys nothing here
# and adds moving parts. The honest shape for "host a model for a batch job" is one VM you
# tear down after. (Scale-to-zero is the right pattern for the *app*, not the model box.)

terraform {
  required_version = ">= 1.5"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
  zone    = var.zone
}

locals {
  # Boot from a models-baked custom image if provided, else the stock Deep Learning image
  # (which then pulls the models on first boot). See var.models_image.
  boot_image = var.models_image != "" ? var.models_image : "projects/deeplearning-platform-release/global/images/family/common-cu129-ubuntu-2204-nvidia-580"
}

# Enable the APIs the deploy needs. Terraform will turn these on; on a brand-new project
# the first apply can take a minute while they propagate.
resource "google_project_service" "compute" {
  service            = "compute.googleapis.com"
  disable_on_destroy = false
}

# A dedicated least-privilege service account for the VM. It needs nothing in GCP (the
# model box only serves HTTP), so we attach NO roles — it can't touch other cloud resources.
# This is the IAM-least-privilege point: the box that runs untrusted-ish model output has
# no standing cloud permissions to abuse.
resource "google_service_account" "vm" {
  account_id   = "oncall-model-vm"
  display_name = "On-Call Copilot model VM (no roles — serves HTTP only)"
}

resource "google_compute_instance" "model" {
  name         = "oncall-model"
  machine_type = var.machine_type
  zone         = var.zone
  tags         = ["oncall-model"] # matches the firewall target_tags below
  depends_on   = [google_project_service.compute]

  # GPU VMs cannot live-migrate — they must TERMINATE on host maintenance.
  # Spot mode (var.use_spot) uses the preemptible capacity pool — often available when on-demand
  # L4s are stocked out; the trade is it can be reclaimed mid-run (retry if so).
  scheduling {
    on_host_maintenance = "TERMINATE"
    automatic_restart   = var.use_spot ? false : true
    provisioning_model  = var.use_spot ? "SPOT" : "STANDARD"
    preemptible         = var.use_spot
  }

  guest_accelerator {
    type  = "nvidia-l4"
    count = 1
  }

  boot_disk {
    initialize_params {
      # Stock Deep Learning image (NVIDIA driver + CUDA preinstalled) OR a models-baked custom
      # image — see local.boot_image / var.models_image. Baked image = no re-download on deploy.
      image = local.boot_image
      size  = var.boot_disk_gb
      type  = "pd-balanced"
    }
  }

  network_interface {
    network = "default"
    access_config {} # ephemeral public IP so you can reach the endpoint + SSH
  }

  service_account {
    email  = google_service_account.vm.email
    scopes = ["cloud-platform"]
  }

  metadata = {
    "install-nvidia-driver" = "True"
    "startup-script" = templatefile("${path.module}/startup.sh", {
      answerer_model = var.answerer_model
      judge_model    = var.judge_model
    })
  }

  labels = {
    purpose  = "oncall-copilot-eval"
    teardown = "after-run" # a human reminder; the cost guardrail is you running destroy
  }
}

# Firewall: expose Ollama (:11434) ONLY to your IP, and SSH (:22) ONLY to your IP.
# Never open the model endpoint to the world — an open Ollama is an open, abusable LLM.
resource "google_compute_firewall" "ollama" {
  name    = "oncall-allow-ollama-ssh"
  network = "default"

  allow {
    protocol = "tcp"
    ports    = ["22", "11434"]
  }

  source_ranges = [var.allowed_cidr]
  target_tags   = ["oncall-model"]
}

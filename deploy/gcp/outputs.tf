# After `terraform apply`, these print the two things you need: how to reach the endpoint
# and how to point the eval at it. The model server takes a few minutes after boot to pull
# weights — see the readiness check in the README before you run the eval.

output "instance_ip" {
  value       = google_compute_instance.model.network_interface[0].access_config[0].nat_ip
  description = "Public IP of the model box."
}

output "selfhosted_base_url" {
  value       = "http://${google_compute_instance.model.network_interface[0].access_config[0].nat_ip}:11434/v1"
  description = "Set SELFHOSTED_BASE_URL to this so the eval talks to your box."
}

output "ssh_command" {
  value       = "gcloud compute ssh oncall-model --zone ${var.zone} --project ${var.project_id}"
  description = "SSH in to watch model pulls / logs (journalctl -u ollama -f)."
}

output "run_eval_hint" {
  value       = "export SELFHOSTED_BASE_URL=http://${google_compute_instance.model.network_interface[0].access_config[0].nat_ip}:11434/v1 PROVIDER_INVESTIGATOR=selfhosted MODEL_INVESTIGATOR=${var.answerer_model} PROVIDER_JUDGE=selfhosted MODEL_JUDGE=${var.judge_model} ; python3 -m evals.run_evals"
  description = "One-liner to run the full eval against the self-hosted models (both role vars required)."
}

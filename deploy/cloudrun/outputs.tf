output "service_url" {
  value       = google_cloud_run_v2_service.app.uri
  description = "HTTPS URL of the deployed service (authenticated unless allow_unauthenticated=true)."
}

output "runtime_service_account" {
  value       = google_service_account.run.email
  description = "The least-privilege identity the service runs as (only reads the LLM key secret)."
}

output "image" {
  value       = var.image
  description = "The deployed image ref."
}

output "curl_authenticated" {
  value       = "curl -H \"Authorization: Bearer $(gcloud auth print-identity-token)\" ${google_cloud_run_v2_service.app.uri}"
  description = "How to reach the service while it's authenticated-only."
}

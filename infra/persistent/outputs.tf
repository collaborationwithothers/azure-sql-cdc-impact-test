# Outputs are for the local bootstrap session only. They are never committed.

output "pipeline_client_id" {
  description = "Client ID of the pipeline identity. Goes into the AZURE_CLIENT_ID environment secret."
  value       = azurerm_user_assigned_identity.pipeline.client_id
  sensitive   = true
}

output "storage_account_name" {
  description = "Account holding Terraform state and results."
  value       = azurerm_storage_account.this.name
  sensitive   = true
}

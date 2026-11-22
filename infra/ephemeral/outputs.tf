output "job_name" {
  value = azurerm_container_app_job.rig.name
}

output "environment_name" {
  value     = azurerm_container_app_environment.this.name
  sensitive = true
}

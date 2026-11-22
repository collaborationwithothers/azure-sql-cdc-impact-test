# Every variable has a default so that destroy and sweep run with only the
# discovered names set.

variable "resource_group_name" {
  type    = string
  default = "rg-cdc-impact-test"
}

variable "run_id" {
  description = "Run identifier. Tagged on every resource and carried by every result record."
  type        = string
  default     = "none"
}

variable "deadline" {
  description = "UTC time, RFC 3339, after which sweep destroys the layer."
  type        = string
  default     = "1970-01-01T00:00:00Z"
}

# Names of persistent resources, discovered by tag in the pipeline.
variable "storage_account_name" {
  type    = string
  default = ""
}

variable "acr_name" {
  type    = string
  default = ""
}

variable "log_analytics_workspace_name" {
  type    = string
  default = ""
}

variable "application_insights_name" {
  type    = string
  default = ""
}

variable "rig_identity_name" {
  type    = string
  default = "id-cdc-impact-rig"
}

variable "pool_vcores" {
  description = "Pool size: 4 for the main runs, 6 for the confirming run (design section 2)."
  type        = number
  default     = 4
  validation {
    condition     = contains([4, 6, 8, 10, 12, 14, 16], var.pool_vcores)
    error_message = "General Purpose standard-series pool sizes from 4 to 16 vCores only."
  }
}

variable "per_database_max_vcores" {
  description = "Per-database vCore cap (design section 2: 2)."
  type        = number
  default     = 2
}

variable "pool_max_size_gb" {
  type    = number
  default = 100
}

variable "database_max_size_gb" {
  type    = number
  default = 40
}

variable "eventhubs_throughput_units" {
  type    = number
  default = 10
}

variable "eventhubs_max_throughput_units" {
  type    = number
  default = 20
}

variable "workload_profile_type" {
  description = "Dedicated profile. D8 is 8 vCPU and 32 GiB per node in Sweden Central (observed 2026-11-22)."
  type        = string
  default     = "D8"
}

# The two containers share one D8 node. The platform reserves part of the
# node; the exact amount is not documented, so the total stays below 8 vCPU.
variable "rig_cpu" {
  type    = number
  default = 4
}

variable "rig_memory" {
  type    = string
  default = "16Gi"
}

variable "debezium_cpu" {
  type    = number
  default = 2.5
}

variable "debezium_memory" {
  type    = string
  default = "10Gi"
}

variable "debezium_heap" {
  type    = string
  default = "6g"
}

variable "replica_timeout_seconds" {
  type    = number
  default = 10800
}

variable "rig_image" {
  type    = string
  default = "mcr.microsoft.com/k8se/quickstart-jobs:latest"
}

variable "debezium_image" {
  type    = string
  default = "mcr.microsoft.com/k8se/quickstart-jobs:latest"
}

variable "rig_settings_json" {
  description = "Orchestrator settings (src/rig/cdcrig/settings.py). Run-specific names are merged in by Terraform."
  type        = string
  default     = "{\"mode\":\"smoke\"}"
}

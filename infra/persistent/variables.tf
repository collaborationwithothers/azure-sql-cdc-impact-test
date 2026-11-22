variable "resource_group_name" {
  description = "The project's resource group. Every resource in both layers lives here."
  type        = string
  default     = "rg-cdc-impact-test"
}

variable "location" {
  description = "Azure region. Fixed by the design (section 2)."
  type        = string
  default     = "swedencentral"
}

variable "github_subject_prefix" {
  description = "OIDC subject prefix of the repository, read at bootstrap from GET /repos/{owner}/{repo}/actions/oidc/customization/sub (field sub_claim_prefix). The repo uses GitHub's immutable subject format."
  type        = string
}

variable "github_environment" {
  description = "GitHub environment whose jobs may sign in as the pipeline identity."
  type        = string
  default     = "azure"
}

variable "monthly_budget" {
  description = "Monthly budget for the resource group, in the subscription's billing currency."
  type        = number
  default     = 50
}

variable "budget_start_date" {
  description = "First day of the month in which the budget starts, RFC 3339."
  type        = string
  default     = "2026-11-01T00:00:00Z"
}

variable "budget_contact_emails" {
  description = "Budget alert recipients. Supplied at bootstrap, never committed."
  type        = list(string)
  sensitive   = true
}

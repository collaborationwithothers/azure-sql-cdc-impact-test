# Persistent layer. Applied once, from a local session, by a person or agent
# with Owner rights on the subscription. See docs/bootstrap.md.
#
# Names that must be globally unique get a random suffix. Nothing here is
# looked up by name in the pipeline: the pipeline finds each resource by its
# "purpose" tag, so no generated name is ever committed.

locals {
  tags = {
    project = "azure-sql-cdc-impact-test"
    layer   = "persistent"
  }
  suffix = random_string.suffix.result
}

resource "random_string" "suffix" {
  length  = 8
  upper   = false
  special = false
}

resource "azurerm_resource_group" "this" {
  name     = var.resource_group_name
  location = var.location
  tags     = local.tags
}

# ---------------------------------------------------------------------------
# Storage: one account, two containers. "tfstate" holds Terraform state for
# both layers. "results" holds raw run output written by the test rig.
# Shared key access is off. Everything authenticates with Entra ID.
# ---------------------------------------------------------------------------

resource "azurerm_storage_account" "this" {
  name                            = "stcdcimpact${local.suffix}"
  resource_group_name             = azurerm_resource_group.this.name
  location                        = azurerm_resource_group.this.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  account_kind                    = "StorageV2"
  min_tls_version                 = "TLS1_2"
  shared_access_key_enabled       = false
  allow_nested_items_to_be_public = false
  default_to_oauth_authentication = true

  blob_properties {
    versioning_enabled = true
    delete_retention_policy {
      days = 14
    }
    container_delete_retention_policy {
      days = 14
    }
  }

  tags = merge(local.tags, { purpose = "state-and-results" })

  # This account holds the persistent layer's own state. Terraform must refuse
  # to destroy it.
  lifecycle {
    prevent_destroy = true
  }
}

resource "azurerm_storage_container" "tfstate" {
  name                  = "tfstate"
  storage_account_id    = azurerm_storage_account.this.id
  container_access_type = "private"
}

resource "azurerm_storage_container" "results" {
  name                  = "results"
  storage_account_id    = azurerm_storage_account.this.id
  container_access_type = "private"
}

# ---------------------------------------------------------------------------
# Log Analytics. Persistent because platform metrics streamed here by the
# ephemeral layer's diagnostic settings must outlive the pool.
# ---------------------------------------------------------------------------

resource "azurerm_log_analytics_workspace" "this" {
  name                = "log-cdc-impact-${local.suffix}"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  sku                 = "PerGB2018"
  retention_in_days   = 30
  daily_quota_gb      = 5
  tags                = merge(local.tags, { purpose = "diagnostics" })
}

# Application Insights on the same workspace (decision 0011). Local
# authentication is off, so only Entra-authenticated telemetry is accepted and
# the connection string carries no write access.
resource "azurerm_application_insights" "this" {
  name                         = "appi-cdc-impact-${local.suffix}"
  resource_group_name          = azurerm_resource_group.this.name
  location                     = azurerm_resource_group.this.location
  workspace_id                 = azurerm_log_analytics_workspace.this.id
  application_type             = "other"
  local_authentication_enabled = false
  retention_in_days            = 30
  tags                         = merge(local.tags, { purpose = "telemetry" })
}

# ---------------------------------------------------------------------------
# Container registry for the test-rig image. See
# docs/decisions/0003-container-registry.md.
# ---------------------------------------------------------------------------

resource "azurerm_container_registry" "this" {
  name                = "acrcdcimpact${local.suffix}"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  sku                 = "Basic"
  admin_enabled       = false
  tags                = merge(local.tags, { purpose = "images" })
}

# ---------------------------------------------------------------------------
# Identities.
# ---------------------------------------------------------------------------

# Pipeline identity. GitHub Actions signs in as this identity through OpenID
# Connect federation. The federated credential accepts tokens only for jobs
# that run in the repository's "azure" environment, and that environment
# accepts deployments from main only.
resource "azurerm_user_assigned_identity" "pipeline" {
  name                = "id-cdc-impact-pipeline"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  tags                = merge(local.tags, { purpose = "pipeline-identity" })
}

resource "azurerm_federated_identity_credential" "github_azure_environment" {
  name                      = "github-azure-environment"
  user_assigned_identity_id = azurerm_user_assigned_identity.pipeline.id
  audience                  = ["api://AzureADTokenExchange"]
  issuer                    = "https://token.actions.githubusercontent.com"
  subject                   = "${var.github_subject_prefix}:environment:${var.github_environment}"
}

# Test-rig identity. The Container Apps job runs as this identity. It is also
# the Microsoft Entra admin of each ephemeral SQL server, so it can create the
# schema and enable CDC (see docs/decisions/0004-sql-identity.md).
resource "azurerm_user_assigned_identity" "rig" {
  name                = "id-cdc-impact-rig"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  tags                = merge(local.tags, { purpose = "rig-identity" })
}

# ---------------------------------------------------------------------------
# Role assignments. The pipeline identity is Contributor on this resource
# group only, and cannot create role assignments. Every data-plane role the
# ephemeral layer needs is therefore granted here, once.
# ---------------------------------------------------------------------------

resource "azurerm_role_assignment" "pipeline_contributor" {
  scope                = azurerm_resource_group.this.id
  role_definition_name = "Contributor"
  principal_id         = azurerm_user_assigned_identity.pipeline.principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "pipeline_blob" {
  scope                = azurerm_storage_account.this.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.pipeline.principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "pipeline_acr_push" {
  scope                = azurerm_container_registry.this.id
  role_definition_name = "AcrPush"
  principal_id         = azurerm_user_assigned_identity.pipeline.principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "rig_acr_pull" {
  scope                = azurerm_container_registry.this.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.rig.principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "rig_results" {
  scope                = azurerm_storage_container.results.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.rig.principal_id
  principal_type       = "ServicePrincipal"
}

# The Event Hubs namespace is ephemeral, so its roles are granted at resource
# group scope, where they will apply to whichever namespace a run creates.
resource "azurerm_role_assignment" "rig_eventhubs_sender" {
  scope                = azurerm_resource_group.this.id
  role_definition_name = "Azure Event Hubs Data Sender"
  principal_id         = azurerm_user_assigned_identity.rig.principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "rig_eventhubs_receiver" {
  scope                = azurerm_resource_group.this.id
  role_definition_name = "Azure Event Hubs Data Receiver"
  principal_id         = azurerm_user_assigned_identity.rig.principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "rig_telemetry" {
  scope                = azurerm_application_insights.this.id
  role_definition_name = "Monitoring Metrics Publisher"
  principal_id         = azurerm_user_assigned_identity.rig.principal_id
  principal_type       = "ServicePrincipal"
}

# The person or agent running the bootstrap needs blob data access to migrate
# the persistent state into the account and to read results locally.
data "azurerm_client_config" "current" {}

resource "azurerm_role_assignment" "bootstrapper_blob" {
  scope                = azurerm_storage_account.this.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = data.azurerm_client_config.current.object_id
}

# ---------------------------------------------------------------------------
# Budget alert. Must exist before the first apply of the ephemeral layer.
# Notifications go to Owner role holders and to emails supplied at bootstrap
# time. Azure requires at least one email at resource group scope. The emails
# are a bootstrap input and are never committed.
# ---------------------------------------------------------------------------

resource "azurerm_consumption_budget_resource_group" "this" {
  name              = "budget-cdc-impact"
  resource_group_id = azurerm_resource_group.this.id
  amount            = var.monthly_budget
  time_grain        = "Monthly"

  time_period {
    start_date = var.budget_start_date
  }

  notification {
    enabled        = true
    threshold      = 50
    operator       = "GreaterThan"
    threshold_type = "Actual"
    contact_roles  = ["Owner"]
    contact_emails = var.budget_contact_emails
  }

  notification {
    enabled        = true
    threshold      = 80
    operator       = "GreaterThan"
    threshold_type = "Actual"
    contact_roles  = ["Owner"]
    contact_emails = var.budget_contact_emails
  }

  notification {
    enabled        = true
    threshold      = 100
    operator       = "GreaterThan"
    threshold_type = "Forecasted"
    contact_roles  = ["Owner"]
    contact_emails = var.budget_contact_emails
  }

  lifecycle {
    ignore_changes = [time_period]
  }
}

# ---------------------------------------------------------------------------
# Diagnostic settings: every platform log and metric category of every
# resource that supports diagnostic settings goes to the workspace.
# - Managed identities, role assignments and the budget have no diagnostic
#   settings in Azure.
# - Application Insights is workspace-based: its telemetry is already stored
#   in this workspace, so a diagnostic setting would only duplicate it.
# - Storage: the account carries metrics; the blob service carries the
#   read, write and delete logs.
# ---------------------------------------------------------------------------

module "diagnostics" {
  source                     = "../modules/diagnostics"
  log_analytics_workspace_id = azurerm_log_analytics_workspace.this.id
  targets = {
    storage_account = azurerm_storage_account.this.id
    blob_service    = "${azurerm_storage_account.this.id}/blobServices/default"
    log_analytics   = azurerm_log_analytics_workspace.this.id
    registry        = azurerm_container_registry.this.id
  }
}

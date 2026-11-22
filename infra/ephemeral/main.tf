# Ephemeral layer: everything that exists only during a run. Applied and
# destroyed by the run-test workflow only (AGENTS.md hard safety rules).
# Every resource carries a deadline tag; the sweep workflow destroys anything
# past it. No virtual network (decision 0008).

locals {
  tags = {
    project  = "azure-sql-cdc-impact-test"
    layer    = "ephemeral"
    run_id   = var.run_id
    deadline = var.deadline
  }
  suffix = random_string.suffix.result
}

resource "random_string" "suffix" {
  length  = 8
  upper   = false
  special = false
}

data "azurerm_resource_group" "this" {
  name = var.resource_group_name
}

data "azurerm_user_assigned_identity" "rig" {
  name                = var.rig_identity_name
  resource_group_name = var.resource_group_name
}

data "azurerm_log_analytics_workspace" "this" {
  name                = var.log_analytics_workspace_name
  resource_group_name = var.resource_group_name
}

data "azurerm_container_registry" "this" {
  name                = var.acr_name
  resource_group_name = var.resource_group_name
}

data "azurerm_application_insights" "this" {
  name                = var.application_insights_name
  resource_group_name = var.resource_group_name
}

# ---------------------------------------------------------------------------
# SQL: logical server, zone-redundant General Purpose pool, two databases.
# ---------------------------------------------------------------------------

resource "azurerm_mssql_server" "this" {
  name                          = "sql-cdc-impact-${local.suffix}"
  resource_group_name           = data.azurerm_resource_group.this.name
  location                      = data.azurerm_resource_group.this.location
  version                       = "12.0"
  minimum_tls_version           = "1.2"
  public_network_access_enabled = true

  # Entra-only. The rig identity is the Entra admin (decision 0004), so it is
  # dbo in every user database and can enable CDC. Microsoft Learn says the
  # identifier for an application admin is its application (client) ID.
  azuread_administrator {
    login_username              = data.azurerm_user_assigned_identity.rig.name
    object_id                   = data.azurerm_user_assigned_identity.rig.client_id
    azuread_authentication_only = true
  }

  tags = local.tags
}

# "Allow Azure services": the Container Apps environment has no fixed,
# documented outbound address. Only Entra sign-in is accepted (decision 0008).
resource "azurerm_mssql_firewall_rule" "azure_services" {
  name             = "AllowAllWindowsAzureIps"
  server_id        = azurerm_mssql_server.this.id
  start_ip_address = "0.0.0.0"
  end_ip_address   = "0.0.0.0"
}

resource "azurerm_mssql_elasticpool" "this" {
  name                = "pool-cdc-impact"
  resource_group_name = data.azurerm_resource_group.this.name
  location            = data.azurerm_resource_group.this.location
  server_name         = azurerm_mssql_server.this.name
  license_type        = "LicenseIncluded"
  max_size_gb         = var.pool_max_size_gb
  zone_redundant      = true

  sku {
    name     = "GP_Gen5"
    tier     = "GeneralPurpose"
    family   = "Gen5"
    capacity = var.pool_vcores
  }

  per_database_settings {
    min_capacity = 0
    max_capacity = var.per_database_max_vcores
  }

  tags = local.tags
}

resource "azurerm_mssql_database" "db" {
  for_each             = toset(["cdcdb1", "cdcdb2"])
  name                 = each.key
  server_id            = azurerm_mssql_server.this.id
  elastic_pool_id      = azurerm_mssql_elasticpool.this.id
  max_size_gb          = var.database_max_size_gb
  storage_account_type = "Zone"
  # Must match the pool. The provider sends false when this is unset, and the
  # API rejects it: "The zone resilience value of the database and elastic
  # pool should match." (spike run 45049809770).
  zone_redundant = true
  tags           = local.tags
}

# ---------------------------------------------------------------------------
# Event Hubs: Debezium's publish target (decision D3), reached through the
# Kafka endpoint with Entra tokens. SAS keys are disabled.
# ---------------------------------------------------------------------------

resource "azurerm_eventhub_namespace" "this" {
  name                         = "evhns-cdc-impact-${local.suffix}"
  resource_group_name          = data.azurerm_resource_group.this.name
  location                     = data.azurerm_resource_group.this.location
  sku                          = "Standard"
  capacity                     = var.eventhubs_throughput_units
  auto_inflate_enabled         = true
  maximum_throughput_units     = var.eventhubs_max_throughput_units
  local_authentication_enabled = false
  minimum_tls_version          = "1.2"
  tags                         = local.tags
}

# Debezium topic name is <topic.prefix>.<database>.<schema>.<table>.
# Kafka topic auto-creation is not documented for Event Hubs, so it is created here.
resource "azurerm_eventhub" "outbox" {
  name              = "cdc.cdcdb1.dbo.Outbox"
  namespace_id      = azurerm_eventhub_namespace.this.id
  partition_count   = 32
  message_retention = 1
}

# ---------------------------------------------------------------------------
# Container Apps: environment with one dedicated workload profile, and the
# test-rig job with two containers (decision 0010). Retries are zero: an
# interrupted benchmark is failed, not retried (design section 5).
# Logs go through the environment's diagnostic setting (decision 0012).
# ---------------------------------------------------------------------------

resource "azurerm_container_app_environment" "this" {
  name                = "cae-cdc-impact-${local.suffix}"
  resource_group_name = data.azurerm_resource_group.this.name
  location            = data.azurerm_resource_group.this.location
  logs_destination    = "azure-monitor"

  workload_profile {
    name                  = "rig"
    workload_profile_type = var.workload_profile_type
    minimum_count         = 1
    maximum_count         = 1
  }

  tags = local.tags
}

locals {
  shared_env = {
    AZURE_CLIENT_ID                       = data.azurerm_user_assigned_identity.rig.client_id
    APPLICATIONINSIGHTS_CONNECTION_STRING = data.azurerm_application_insights.this.connection_string
  }
  rig_settings = jsonencode(merge(jsondecode(var.rig_settings_json), {
    runId              = var.run_id
    sqlServer          = azurerm_mssql_server.this.fully_qualified_domain_name
    resultsAccount     = var.storage_account_name
    eventHubsNamespace = azurerm_eventhub_namespace.this.name
    eventHubName       = azurerm_eventhub.outbox.name
  }))
}

resource "azurerm_container_app_job" "rig" {
  name                         = "job-cdc-impact-rig"
  resource_group_name          = data.azurerm_resource_group.this.name
  location                     = data.azurerm_resource_group.this.location
  container_app_environment_id = azurerm_container_app_environment.this.id
  workload_profile_name        = "rig"
  replica_timeout_in_seconds   = var.replica_timeout_seconds
  replica_retry_limit          = 0

  manual_trigger_config {
    parallelism              = 1
    replica_completion_count = 1
  }

  identity {
    type         = "UserAssigned"
    identity_ids = [data.azurerm_user_assigned_identity.rig.id]
  }

  registry {
    server   = data.azurerm_container_registry.this.login_server
    identity = data.azurerm_user_assigned_identity.rig.id
  }

  template {
    volume {
      name         = "shared"
      storage_type = "EmptyDir"
    }

    container {
      name   = "rig"
      image  = var.rig_image
      cpu    = var.rig_cpu
      memory = var.rig_memory

      dynamic "env" {
        for_each = merge(local.shared_env, { RIG_SETTINGS = local.rig_settings })
        content {
          name  = env.key
          value = env.value
        }
      }

      volume_mounts {
        name = "shared"
        path = "/shared"
      }
    }

    container {
      name   = "debezium"
      image  = var.debezium_image
      cpu    = var.debezium_cpu
      memory = var.debezium_memory

      dynamic "env" {
        for_each = merge(local.shared_env, { JAVA_OPTS = "-Xms1g -Xmx${var.debezium_heap}" })
        content {
          name  = env.key
          value = env.value
        }
      }

      volume_mounts {
        name = "shared"
        path = "/shared"
      }
    }
  }

  tags = local.tags

  depends_on = [azurerm_mssql_database.db, azurerm_mssql_firewall_rule.azure_services]
}

# ---------------------------------------------------------------------------
# Diagnostic settings on every resource that supports them (decision 0012).
# ---------------------------------------------------------------------------

module "diagnostics" {
  source                     = "../modules/diagnostics"
  log_analytics_workspace_id = data.azurerm_log_analytics_workspace.this.id
  targets = {
    pool        = azurerm_mssql_elasticpool.this.id
    cdcdb1      = azurerm_mssql_database.db["cdcdb1"].id
    cdcdb2      = azurerm_mssql_database.db["cdcdb2"].id
    master      = "${azurerm_mssql_server.this.id}/databases/master"
    eventhubs   = azurerm_eventhub_namespace.this.id
    environment = azurerm_container_app_environment.this.id
  }
}

# Sends every platform log and metric category a resource supports to one
# Log Analytics workspace. Categories are discovered per resource, so nothing
# is hard-coded and nothing is missed. Where a resource supports the
# "allLogs" category group it is used; otherwise each log category is listed.

terraform {
  required_providers {
    azurerm = {
      source = "hashicorp/azurerm"
    }
  }
}

variable "targets" {
  description = "Map of a static key to the resource ID that gets a diagnostic setting."
  type        = map(string)
}

variable "log_analytics_workspace_id" {
  type = string
}

data "azurerm_monitor_diagnostic_categories" "this" {
  for_each    = var.targets
  resource_id = each.value
}

resource "azurerm_monitor_diagnostic_setting" "this" {
  for_each                   = var.targets
  name                       = "to-log-analytics"
  target_resource_id         = each.value
  log_analytics_workspace_id = var.log_analytics_workspace_id

  dynamic "enabled_log" {
    for_each = contains(data.azurerm_monitor_diagnostic_categories.this[each.key].log_category_groups, "allLogs") ? ["allLogs"] : []
    content {
      category_group = enabled_log.value
    }
  }

  dynamic "enabled_log" {
    for_each = contains(data.azurerm_monitor_diagnostic_categories.this[each.key].log_category_groups, "allLogs") ? [] : data.azurerm_monitor_diagnostic_categories.this[each.key].log_category_types
    content {
      category = enabled_log.value
    }
  }

  dynamic "enabled_metric" {
    for_each = data.azurerm_monitor_diagnostic_categories.this[each.key].metrics
    content {
      category = enabled_metric.value
    }
  }
}

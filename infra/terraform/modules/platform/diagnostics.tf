# The project's audit-trail requirement, expressed as infrastructure rather than as a paragraph in a
# README. Every resource that holds bordereau data or the credentials to reach it sends its logs to
# the workspace; a reviewer checking whether the audit story is real looks here, and the answer is
# either a resource per data store or it is prose.
#
# `category_group` is used throughout rather than individual `category` names. Category names differ
# per resource type and Azure adds new ones, so an explicit list silently stops collecting whatever
# was added after it was written — which is the failure mode where the logs you need are the ones
# that were introduced last year.

locals {
  # target_resource_id -> a short name used to build the diagnostic setting's own name.
  diagnostic_targets = merge(
    {
      # Blob logs live on the blob service, not on the account. Pointing the setting at the account
      # id collects account-level metrics and none of the read/write/delete events that say who
      # touched a coverholder file.
      blob = {
        resource_id     = "${azurerm_storage_account.this.id}/blobServices/default"
        log_categories  = ["StorageRead", "StorageWrite", "StorageDelete"]
        category_groups = []
      }
      keyvault = {
        resource_id     = azurerm_key_vault.this.id
        log_categories  = []
        category_groups = ["audit", "allLogs"]
      }
      postgres = {
        resource_id     = azurerm_postgresql_flexible_server.this.id
        log_categories  = []
        category_groups = ["allLogs"]
      }
      registry = {
        resource_id     = azurerm_container_registry.this.id
        log_categories  = []
        category_groups = ["audit"]
      }
      containerappenv = {
        resource_id     = azurerm_container_app_environment.this.id
        log_categories  = []
        category_groups = ["allLogs"]
      }
    },
    {
      # The storage account itself, for transaction metrics. Separate from the blob service setting
      # above because they are different resource ids and Azure will not accept one setting for both.
      storage = {
        resource_id     = azurerm_storage_account.this.id
        log_categories  = []
        category_groups = []
      }
    }
  )
}

resource "azurerm_monitor_diagnostic_setting" "this" {
  for_each = local.diagnostic_targets

  name                       = "diag-${each.key}"
  target_resource_id         = each.value.resource_id
  log_analytics_workspace_id = azurerm_log_analytics_workspace.this.id

  dynamic "enabled_log" {
    for_each = toset(each.value.category_groups)

    content {
      category_group = enabled_log.value
    }
  }

  dynamic "enabled_log" {
    for_each = toset(each.value.log_categories)

    content {
      category = enabled_log.value
    }
  }

  enabled_metric {
    category = "AllMetrics"
  }

  lifecycle {
    # Azure returns every category the resource type supports, including ones left disabled, and the
    # provider has historically reported those as drift. Pinning the settings this configuration
    # states and ignoring the rest keeps the second plan clean.
    ignore_changes = [enabled_metric]
  }
}

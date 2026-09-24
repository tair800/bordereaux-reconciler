locals {
  # One place decides names. Storage, vault and registry names are globally unique across all of
  # Azure and have three different character sets between them, which is why they are built here
  # rather than interpolated at each resource where one of them would eventually be wrong.
  base = "brdx-${var.environment}"
  # Storage and registry allow lowercase alphanumerics only, 24 and 50 characters respectively.
  compact = "brdx${var.environment}${var.name_suffix}"

  names = {
    resource_group     = "rg-${local.base}"
    log_analytics      = "log-${local.base}"
    identity           = "id-${local.base}"
    key_vault          = "kv-${local.base}-${var.name_suffix}"
    storage_account    = "st${local.compact}"
    container_registry = "cr${local.compact}"
    postgres           = "psql-${local.base}-${var.name_suffix}"
    container_app_env  = "cae-${local.base}"
    action_group       = "ag-${local.base}"
    virtual_network    = "vnet-${local.base}"
  }

  # The residency table is read once here and handed to outputs.tf. Terraform and
  # scripts/residency_manifest.py read the same file; see region_jurisdictions.json for why that
  # matters more than it looks.
  region_table = jsondecode(file("${path.module}/../../region_jurisdictions.json"))
  region_facts = local.region_table.regions[var.location]

  tags = merge(var.tags, {
    environment = var.environment
    project     = "bordereaux-reconciler"
    managed_by  = "terraform"
  })

  # A workload-profile environment must be VNet-injected. Stating the dependency here means the
  # error arrives as a plan-time precondition naming the missing variable, instead of an Azure API
  # rejection several minutes into an apply.
  needs_network = length(var.container_app_workload_profiles) > 0 || var.private_endpoints_enabled
}

resource "azurerm_resource_group" "this" {
  name     = local.names.resource_group
  location = var.location
  tags     = local.tags

  lifecycle {
    precondition {
      condition     = !local.needs_network || var.network != null
      error_message = "workload profiles and private endpoints both require var.network to be set."
    }
  }
}

# The single application identity. Every Azure credential the application holds is this identity;
# there is no client secret, no storage account key and no database password anywhere in the system,
# which is what makes "the application has no secret to leak" a structural statement rather than a
# claim about how carefully the secrets are handled.
resource "azurerm_user_assigned_identity" "app" {
  name                = local.names.identity
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  tags                = local.tags
}

resource "azurerm_log_analytics_workspace" "this" {
  name                = local.names.log_analytics
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  sku                 = "PerGB2018"
  retention_in_days   = var.log_retention_days
  daily_quota_gb      = var.log_daily_quota_gb
  tags                = local.tags

  # Shared-key ingestion is the workspace's equivalent of a storage account key: a long-lived
  # credential that exists only so that something can avoid using an identity.
  local_authentication_enabled = false
}

resource "azurerm_monitor_action_group" "budget" {
  name                = local.names.action_group
  resource_group_name = azurerm_resource_group.this.name
  short_name          = substr("brdx${var.environment}", 0, 12)
  tags                = local.tags

  dynamic "email_receiver" {
    for_each = { for idx, address in var.budget.contact_emails : idx => address }

    content {
      name                    = "budget-${email_receiver.key}"
      email_address           = email_receiver.value
      use_common_alert_schema = true
    }
  }
}

# The GPU topology is the one that can spend real money by accident, and it lives in an environment
# that is meant to be created and destroyed around a benchmark run. A budget does not stop an apply
# — nothing in Azure does — but it is the difference between noticing on the day and noticing on the
# invoice.
resource "azurerm_consumption_budget_resource_group" "this" {
  name              = "budget-${local.base}"
  resource_group_id = azurerm_resource_group.this.id
  amount            = var.budget.monthly_amount
  time_grain        = "Monthly"

  time_period {
    start_date = var.budget.start_date
  }

  dynamic "notification" {
    for_each = { for threshold in var.budget.thresholds : tostring(threshold) => threshold }

    content {
      enabled        = true
      threshold      = notification.value
      operator       = "GreaterThanOrEqualTo"
      threshold_type = notification.key == "100" ? "Forecasted" : "Actual"
      contact_groups = [azurerm_monitor_action_group.budget.id]
    }
  }

  lifecycle {
    # `timestamp()` would be the obvious way to keep start_date valid, and it is the wrong one: it
    # changes on every evaluation, so every plan would show a diff forever. A reviewer's first check
    # on this repository is whether a second plan is clean, and a budget that re-plans itself daily
    # would fail it. The date is therefore a committed constant and drift on it is ignored — Azure
    # only enforces "not in the past" when the budget is created.
    ignore_changes = [time_period]
  }
}

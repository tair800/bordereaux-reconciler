locals {
  region_facts = jsondecode(
    file("${path.module}/../../region_jurisdictions.json")
  ).regions[var.location]

  account_name = "oai-brdx-${var.environment}-${var.name_suffix}"
}

resource "azurerm_cognitive_account" "this" {
  name                = local.account_name
  resource_group_name = var.resource_group_name
  location            = var.location
  kind                = "OpenAI"
  sku_name            = var.sku_name
  tags                = var.tags

  # Required for token authentication against the account's data plane, and it must be globally
  # unique. Without it the endpoint only accepts key auth, which would put an API key back into a
  # system that otherwise has none.
  custom_subdomain_name = local.account_name

  # Keys off. The application calls the model as the managed identity granted below.
  local_auth_enabled = false

  public_network_access_enabled = var.public_network_access_enabled

  identity {
    type = "SystemAssigned"
  }
}

# The role assignment that rbac.tf in the platform module explains the absence of: it is here
# because it is scoped to this account, and scoping it here is what stops it from silently widening
# to every cognitive account in the resource group.
resource "azurerm_role_assignment" "openai_user" {
  scope                = azurerm_cognitive_account.this.id
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = var.identity_principal_id

  skip_service_principal_aad_check = true
}

resource "azurerm_cognitive_deployment" "this" {
  name                 = var.deployment.name
  cognitive_account_id = azurerm_cognitive_account.this.id

  model {
    format  = "OpenAI"
    name    = var.deployment.model_name
    version = var.deployment.model_version
  }

  sku {
    name     = var.deployment.sku_name
    capacity = var.deployment.capacity
  }

  # Azure will otherwise move the deployment to a newer model version on its own schedule. For a
  # project that publishes mapping accuracy per topology, a model that changes underneath the
  # measurement turns every published number into a number about some earlier model. Upgrades here
  # are a commit, a re-run of the golden set, and a new number.
  version_upgrade_option = "NoAutoUpgrade"
}

resource "azurerm_monitor_diagnostic_setting" "this" {
  name                       = "diag-openai"
  target_resource_id         = azurerm_cognitive_account.this.id
  log_analytics_workspace_id = var.log_analytics_workspace_id

  enabled_log {
    category_group = "audit"
  }

  enabled_log {
    category_group = "allLogs"
  }

  enabled_metric {
    category = "AllMetrics"
  }

  lifecycle {
    ignore_changes = [enabled_metric]
  }
}

# ---------------------------------------------------------------------------------------------
# `purge_soft_delete_on_destroy` is NOT set here, and looking for it here is the natural mistake.
#
# It is a provider feature, not a resource argument, so it lives in the `features` block of every
# env root's provider configuration. It matters more than most feature flags: destroying a cognitive
# account leaves a soft-deleted account holding its name at tenant scope, and the next apply fails
# with a name conflict against something the portal does not show by default. That is the single
# most common reason a portfolio's Terraform provisions Azure OpenAI exactly once and is never run
# again — and this project's bench environment is destroyed after every benchmark run, so it would
# hit the problem on its second use.
# ---------------------------------------------------------------------------------------------

output "resource_group_name" {
  value = azurerm_resource_group.this.name
}

output "resource_group_id" {
  value = azurerm_resource_group.this.id
}

output "identity_principal_id" {
  value       = azurerm_user_assigned_identity.app.principal_id
  description = "Handed to the inference modules so they can scope their own role assignments."
}

output "identity_client_id" {
  value = azurerm_user_assigned_identity.app.client_id
}

output "log_analytics_workspace_id" {
  value       = azurerm_log_analytics_workspace.this.id
  description = "Inference modules attach their diagnostic settings to the same workspace."
}

output "container_app_environment_id" {
  value = azurerm_container_app_environment.this.id
}

output "container_registry_login_server" {
  value = azurerm_container_registry.this.login_server
}

output "api_fqdn" {
  value = one(azurerm_container_app.api.ingress[*].fqdn)
}

output "key_vault_uri" {
  value = azurerm_key_vault.this.vault_uri
}

output "postgres_fqdn" {
  value = azurerm_postgresql_flexible_server.this.fqdn
}

# ---------------------------------------------------------------------------------------------
# Residency
#
# The part of this module that exists for kill condition H. Each entry names a resource that holds
# or processes bordereau data and states the region it is declared in, with the jurisdiction facts
# resolved from region_jurisdictions.json rather than restated here — restating them is how the
# manifest and the module come to disagree.
#
# `declared_region` is what this configuration asks Azure for. It is not a measurement of where the
# bytes ended up, and this module cannot make one: Terraform sends a create request naming a region
# and Azure decides what to do with it. The distinction is repeated in the emitted manifest because
# it is the one a reader is most likely to drop.
# ---------------------------------------------------------------------------------------------
output "residency" {
  value = {
    region          = var.location
    region_facts    = local.region_facts
    resource_group  = {
      name            = azurerm_resource_group.this.name
      declared_region = azurerm_resource_group.this.location
    }
    storage_account = {
      name            = azurerm_storage_account.this.name
      declared_region = azurerm_storage_account.this.location
      containers      = sort(keys(local.blob_containers))
      versioning      = true
    }
    database = {
      name            = azurerm_postgresql_flexible_server.this.name
      declared_region = azurerm_postgresql_flexible_server.this.location
      password_auth   = false
    }
    container_app_environment = {
      name            = azurerm_container_app_environment.this.name
      declared_region = azurerm_container_app_environment.this.location
    }
    key_vault = {
      name            = azurerm_key_vault.this.name
      declared_region = azurerm_key_vault.this.location
    }
    log_analytics_workspace = {
      name            = azurerm_log_analytics_workspace.this.name
      declared_region = azurerm_log_analytics_workspace.this.location
    }
    private_endpoints_enabled = var.private_endpoints_enabled
    diagnostic_settings       = sort(keys(local.diagnostic_targets))
  }
}

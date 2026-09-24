output "resource_group_name" {
  value = azurerm_resource_group.this.name
}

output "resource_group_id" {
  value = azurerm_resource_group.this.id
}

output "identity_id" {
  value = azurerm_user_assigned_identity.app.id
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

# Names are operational, not residency. They are emitted separately from the manifest below and are
# deliberately not part of it — see the note on that output.
output "resource_names" {
  value = local.names
}

# ---------------------------------------------------------------------------------------------
# Residency
#
# This is the output that exists for kill condition H in DECISIONS.md: "the committed residency
# manifest does not equal what the Terraform module declares."
#
# Two rules shape what is in it.
#
# **Every region comes from the resource, not from the variable.** `azurerm_resource_group.this.
# location` rather than `var.location`. They are the same today because this module places
# everything in one region; reading them back from the resources means that if that ever stops
# being true, the manifest says so instead of continuing to report the input.
#
# **Resource names are not in it.** A name is not a residency fact, and including one would force
# scripts/residency_manifest.py to reimplement this module's naming rules in Python in order to
# render the same document without running a plan — a duplication that would quietly diverge and
# would be reported as a residency discrepancy when it did. Names are published above instead.
#
# And the distinction the manifest must never blur: `declared_region` is the region this
# configuration *asks* Azure for. Terraform cannot measure where bytes physically come to rest, and
# nothing here is a statement that processing in a given region satisfies any legal obligation.
# ---------------------------------------------------------------------------------------------
output "residency" {
  value = {
    region       = azurerm_resource_group.this.location
    region_facts = local.region_facts

    resources = {
      resource_group = {
        declared_region = azurerm_resource_group.this.location
      }
      storage_account = {
        declared_region    = azurerm_storage_account.this.location
        containers         = sort(keys(local.blob_containers))
        versioning_enabled = true
        shared_keys_enabled = false
      }
      database = {
        declared_region       = azurerm_postgresql_flexible_server.this.location
        password_auth_enabled = false
      }
      container_app_environment = {
        declared_region = azurerm_container_app_environment.this.location
      }
      key_vault = {
        declared_region = azurerm_key_vault.this.location
      }
      log_analytics_workspace = {
        declared_region = azurerm_log_analytics_workspace.this.location
      }
    }

    private_endpoints_enabled = var.private_endpoints_enabled
    # Named so a reviewer can check the audit-trail claim against the manifest without opening
    # the configuration: every entry here is a data-holding resource whose logs reach the workspace.
    diagnostic_settings = sort(keys(local.diagnostic_targets))
  }
}

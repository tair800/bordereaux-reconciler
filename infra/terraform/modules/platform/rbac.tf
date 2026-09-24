# Every grant is scoped to one resource. None is scoped to the resource group or the subscription,
# which is the shortcut that makes a later "what can this identity reach?" unanswerable without
# reading the whole configuration.

resource "azurerm_role_assignment" "app_key_vault_secrets_user" {
  scope                = azurerm_key_vault.this.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.app.principal_id

  # A managed identity's principal is provisioned asynchronously by Entra, and a role assignment
  # created in the same apply can land before the directory has replicated it. The provider's
  # existence check for a service principal is the usual victim.
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "app_storage_blob_data_contributor" {
  scope                = azurerm_storage_account.this.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.app.principal_id

  skip_service_principal_aad_check = true
}

# Not one of the three roles the design brief names, and necessary anyway: a Container App that
# pulls from the registry with `registry { identity = ... }` cannot pull without it, and an app that
# cannot pull its image is an app that does not start. Listing it here rather than discovering it
# during the first deployment.
resource "azurerm_role_assignment" "app_acr_pull" {
  scope                = azurerm_container_registry.this.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.app.principal_id

  skip_service_principal_aad_check = true
}

# ---------------------------------------------------------------------------------------------
# Cognitive Services OpenAI User — deliberately NOT here.
#
# It belongs on the Azure OpenAI account, and that account is created by
# modules/inference_azure_openai, which already takes this module's identity as an input. Creating
# the assignment here would mean platform consumed an output of a module that consumes an output of
# platform, and Terraform would reject the module graph as a cycle before it looked at a single
# resource.
#
# The alternative — scoping it at the resource group so platform can create it without knowing the
# account id — grants the application access to every cognitive account anyone ever adds to the
# environment, including ones provisioned for an unrelated experiment. A role assignment that gets
# broader on its own is worse than one declared in a second file.
#
# So the grant lives next to the account it grants on: modules/inference_azure_openai/main.tf.
# ---------------------------------------------------------------------------------------------

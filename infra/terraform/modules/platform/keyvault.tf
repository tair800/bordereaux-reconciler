resource "azurerm_key_vault" "this" {
  name                = local.names.key_vault
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  tenant_id           = var.tenant_id
  sku_name            = "standard"
  tags                = local.tags

  # RBAC rather than access policies. Access policies are per-vault ACLs that no subscription-wide
  # access review can see; role assignments show up in the same place as every other permission in
  # the tenant. On a project whose selling point is an auditable trail, an authorisation model that
  # hides from the audit tooling is the wrong one.
  #
  # This argument is required in azurerm 5.x. In 3.x and 4.x it was the optional
  # `enable_rbac_authorization`, defaulting to false — so a configuration copied from an older
  # example silently produced an access-policy vault.
  rbac_authorization_enabled = true

  # Irreversible, and therefore not on outside prod. See the variable for the 90-day trap.
  purge_protection_enabled = var.key_vault_purge_protection_enabled

  soft_delete_retention_days = 7

  public_network_access_enabled = !var.private_endpoints_enabled

  dynamic "network_acls" {
    for_each = var.private_endpoints_enabled ? [1] : []

    content {
      default_action = "Deny"
      # AzureServices is needed for the Container App platform to resolve Key Vault secret
      # references; without it the app starts and then fails to read its own configuration.
      bypass = "AzureServices"
    }
  }
}

# Secret *values* are deliberately absent from this configuration. A `azurerm_key_vault_secret`
# resource stores its value in Terraform state in plaintext, so managing secrets here would move
# every secret in the system into a blob that a wider group can read than the vault it came from.
# Terraform creates the vault and grants the application identity read access to it; a human or a
# pipeline puts the values in. What the application consumes are Key Vault *references*, wired in
# container_apps.tf.

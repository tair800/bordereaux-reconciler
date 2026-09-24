resource "azurerm_postgresql_flexible_server" "this" {
  name                = local.names.postgres
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location

  version    = var.postgres.version
  sku_name   = var.postgres.sku_name
  storage_mb = var.postgres.storage_mb
  zone       = var.postgres.zone
  tags       = local.tags

  backup_retention_days        = var.environment == "prod" ? 35 : 7
  geo_redundant_backup_enabled = var.environment == "prod"

  # There is no database password in this system. Not a strong one, not one in Key Vault — none at
  # all, because the server refuses to have an administrator login. That removes the credential
  # behind most PostgreSQL incidents and it removes the argument about rotation, at the cost of
  # making Entra the single point of access failure. The trade is deliberate and the failure mode is
  # the safe direction.
  authentication {
    active_directory_auth_enabled = true
    password_auth_enabled         = false
    tenant_id                     = var.tenant_id
  }

  # VNet-injected in prod, publicly addressable but firewalled-to-nothing elsewhere. The two are
  # mutually exclusive on Flexible Server: supplying a delegated subnet and leaving public access on
  # is rejected by the API.
  delegated_subnet_id = var.private_endpoints_enabled ? azurerm_subnet.postgres[0].id : null
  private_dns_zone_id = var.private_endpoints_enabled ? azurerm_private_dns_zone.this["postgres"].id : null

  public_network_access_enabled = !var.private_endpoints_enabled

  dynamic "high_availability" {
    for_each = var.environment == "prod" ? [1] : []

    content {
      mode = "ZoneRedundant"
    }
  }

  lifecycle {
    # zone is assigned by Azure when omitted and then reported back, which produces a permanent diff
    # on every subsequent plan if the configuration does not name one. It is named in the env
    # declarations; this guards the case where a region reassigns it.
    ignore_changes = [zone]
  }

  depends_on = [azurerm_private_dns_zone_virtual_network_link.this]
}

# The application identity is the database administrator. Combined with password_auth_enabled =
# false this means the only way into the database is to hold this identity or to be added as a
# second Entra administrator — there is no connection string that grants access on its own.
resource "azurerm_postgresql_flexible_server_active_directory_administrator" "app" {
  server_name         = azurerm_postgresql_flexible_server.this.name
  resource_group_name = azurerm_resource_group.this.name
  tenant_id           = var.tenant_id
  object_id           = var.entra_admin.object_id
  principal_name      = var.entra_admin.principal_name
  principal_type      = var.entra_admin.principal_type
}

resource "azurerm_postgresql_flexible_server_database" "ledger" {
  name      = "bordereaux"
  server_id = azurerm_postgresql_flexible_server.this.id

  # Money is compared, grouped and keyed by string identifiers that arrive from coverholder files in
  # whatever case and accent the sender used. A deterministic, locale-independent collation means a
  # policy reference sorts and compares the same way in every environment; the default collation of
  # the server's locale does not guarantee that across regions.
  charset   = "UTF8"
  collation = "en_US.utf8"

  lifecycle {
    # Dropping a database because a collation string changed is not a diff anybody wants applied.
    prevent_destroy = true
  }
}

resource "azurerm_postgresql_flexible_server_firewall_rule" "allowed" {
  for_each = var.private_endpoints_enabled ? {} : var.allowed_client_ip_ranges

  name             = each.key
  server_id        = azurerm_postgresql_flexible_server.this.id
  start_ip_address = each.value.start_ip
  end_ip_address   = each.value.end_ip
}

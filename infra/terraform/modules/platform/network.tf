# A VNet exists only where something genuinely requires one: workload profiles for the GPU topology,
# private endpoints for prod. Environments that need neither create nothing in this file, which is
# why every resource here is behind the same `count`.

locals {
  network_enabled = var.network != null

  private_dns_zones = var.private_endpoints_enabled ? {
    blob     = "privatelink.blob.core.windows.net"
    vault    = "privatelink.vaultcore.azure.net"
    registry = "privatelink.azurecr.io"
    postgres = "privatelink.postgres.database.azure.com"
  } : {}
}

resource "azurerm_virtual_network" "this" {
  count = local.network_enabled ? 1 : 0

  name                = local.names.virtual_network
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  address_space       = [var.network.address_space]
  tags                = local.tags
}

# Container Apps requires this subnet to be delegated to it and to be at least a /27 for workload
# profile environments. Azure will not tell you that until the environment fails to create.
resource "azurerm_subnet" "infrastructure" {
  count = local.network_enabled ? 1 : 0

  name                 = "snet-cae-infra"
  resource_group_name  = azurerm_resource_group.this.name
  virtual_network_name = azurerm_virtual_network.this[0].name
  address_prefixes     = [var.network.infrastructure_subnet]

  delegation {
    name = "container-app-environment"

    service_delegation {
      name    = "Microsoft.App/environments"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

# PostgreSQL Flexible Server VNet injection needs a subnet delegated to it and used by nothing else.
# It is a different subnet from the one above and cannot be shared with it.
resource "azurerm_subnet" "postgres" {
  count = local.network_enabled ? 1 : 0

  name                 = "snet-postgres"
  resource_group_name  = azurerm_resource_group.this.name
  virtual_network_name = azurerm_virtual_network.this[0].name
  address_prefixes     = [var.network.postgres_subnet]

  delegation {
    name = "postgres-flexible-server"

    service_delegation {
      name    = "Microsoft.DBforPostgreSQL/flexibleServers"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

resource "azurerm_subnet" "private_endpoints" {
  count = local.network_enabled ? 1 : 0

  name                 = "snet-private-endpoints"
  resource_group_name  = azurerm_resource_group.this.name
  virtual_network_name = azurerm_virtual_network.this[0].name
  address_prefixes     = [var.network.private_endpoint_subnet]
}

resource "azurerm_private_dns_zone" "this" {
  for_each = local.private_dns_zones

  name                = each.value
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "this" {
  for_each = local.private_dns_zones

  name = "link-${each.key}"
  # azurerm 5.x takes the zone id here. Earlier majors took resource_group_name plus
  # private_dns_zone_name, so this is another line an older example gets wrong.
  private_dns_zone_id   = azurerm_private_dns_zone.this[each.key].id
  virtual_network_id    = azurerm_virtual_network.this[0].id
  registration_enabled  = false
  tags                  = local.tags
}

locals {
  # Each private endpoint is (target resource, sub-resource name, DNS zone key). Keeping them in one
  # map means adding a fourth privately-reachable service is a map entry rather than a resource.
  private_endpoint_targets = var.private_endpoints_enabled ? {
    blob = {
      resource_id  = azurerm_storage_account.this.id
      subresource  = "blob"
      dns_zone_key = "blob"
    }
    vault = {
      resource_id  = azurerm_key_vault.this.id
      subresource  = "vault"
      dns_zone_key = "vault"
    }
    registry = {
      resource_id  = azurerm_container_registry.this.id
      subresource  = "registry"
      dns_zone_key = "registry"
    }
  } : {}
}

resource "azurerm_private_endpoint" "this" {
  for_each = local.private_endpoint_targets

  name                = "pe-${local.base}-${each.key}"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  subnet_id           = azurerm_subnet.private_endpoints[0].id
  tags                = local.tags

  private_service_connection {
    name                           = "psc-${each.key}"
    private_connection_resource_id = each.value.resource_id
    subresource_names              = [each.value.subresource]
    is_manual_connection           = false
  }

  # Without this group the endpoint exists and nothing resolves to it, which presents as an
  # application that times out rather than one that is misconfigured.
  private_dns_zone_group {
    name                 = "default"
    private_dns_zone_ids = [azurerm_private_dns_zone.this[each.value.dns_zone_key].id]
  }
}

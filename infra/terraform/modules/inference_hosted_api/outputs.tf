locals {
  region_table = jsondecode(file("${path.module}/../../region_jurisdictions.json")).regions

  # If the operator happens to declare an Azure region name, the shared table can check the country
  # they claimed. That is the only cross-check available for this topology, so it is taken.
  known_region = contains(keys(local.region_table), var.declared_region)
}

# The one thing this module can actually enforce: a declaration that contradicts the shared table
# where the table has an opinion. It cannot verify anything the table does not cover, and it does
# not pretend to.
check "declared_jurisdiction_is_consistent" {
  assert {
    condition = !local.known_region || (
      local.region_table[var.declared_region].country_iso == var.declared_country_iso
    )
    error_message = format(
      "declared_country_iso %q contradicts region_jurisdictions.json for region %q.",
      var.declared_country_iso,
      var.declared_region,
    )
  }
}

output "residency" {
  value = {
    topology      = "hosted_api"
    provisioned   = false
    provider      = var.provider_label
    endpoint_host = var.endpoint_host

    declared_region    = var.declared_region
    country_iso        = var.declared_country_iso
    residency_boundary = var.declared_residency_boundary

    # The fields that keep this honest. Terraform provisions nothing here, so it can attest to
    # nothing here; the manifest carries who said it and whether anything checked it.
    verified_by_terraform = false
    region_table_used     = local.known_region
    declaration_source    = var.declaration_source

    key_vault_secret_name = var.key_vault_secret_name
  }
}

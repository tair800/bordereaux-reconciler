output "internal_fqdn" {
  value = one(azurerm_container_app.inference.ingress[*].fqdn)
}

output "residency" {
  value = {
    topology        = "selfhost_gpu"
    provisioned     = true
    declared_region = var.location

    geography          = local.region_facts.geography
    country_iso        = local.region_facts.country_iso
    residency_boundary = local.region_facts.residency_boundary

    # The only topology of the three where the processing scope is not a vendor's routing decision:
    # the request reaches a container in a named region and goes nowhere else, because ingress is
    # internal and the replica has no other caller.
    processing_scope = "single-region"
    model            = var.model_id

    verified_by_terraform = true
    region_table_used     = true
  }
}

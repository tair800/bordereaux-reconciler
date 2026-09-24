output "internal_fqdn" {
  value = one(azurerm_container_app.inference.ingress[*].fqdn)
}

output "residency" {
  value = {
    topology    = "selfhost_gpu"
    provisioned = true
    # Read back from the app rather than echoed from var.location. A Container App inherits its
    # region from its environment, so declaring a GPU region that differs from the platform's does
    # not move the replica — it just makes the declaration untrue. Reading the resource is what
    # turns that into a manifest mismatch instead of a quiet lie.
    declared_region = azurerm_container_app.inference.location

    geography          = local.region_facts.geography
    country_iso        = local.region_facts.country_iso
    residency_boundary = local.region_facts.residency_boundary

    # The only topology of the three where the processing scope is not a vendor's routing decision:
    # the request reaches a container in a named region and goes nowhere else, because ingress is
    # internal and the replica has no other caller.
    processing_scope = "single-region"
    model            = var.model_id
  }
}

output "endpoint" {
  value = azurerm_cognitive_account.this.endpoint
}

output "account_name" {
  value = azurerm_cognitive_account.this.name
}

output "deployment_name" {
  value = azurerm_cognitive_deployment.this.name
}

# Every field here is derivable from the committed declaration plus region_jurisdictions.json, which
# is what lets scripts/residency_manifest.py render the same object without a plan. Anything that
# could only be known after an apply — the endpoint URL, the account's principal id — is published
# above instead of being folded in here.
output "residency" {
  value = {
    topology        = "azure_openai"
    provisioned     = true
    declared_region = azurerm_cognitive_account.this.location

    geography          = local.region_facts.geography
    country_iso        = local.region_facts.country_iso
    residency_boundary = local.region_facts.residency_boundary

    # Where the request is processed is decided by the deployment SKU, not by the account's region.
    # A GlobalStandard deployment created in Sweden Central may be served from anywhere Microsoft
    # runs the model, so this field carries the SKU rather than repeating declared_region — the two
    # answer different questions and conflating them is how a residency claim becomes false without
    # anything in the configuration looking wrong.
    processing_scope = var.deployment.sku_name

    model = "${var.deployment.model_name}:${var.deployment.model_version}"
  }
}

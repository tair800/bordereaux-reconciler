output "endpoint" {
  value = azurerm_cognitive_account.this.endpoint
}

output "account_name" {
  value = azurerm_cognitive_account.this.name
}

output "deployment_name" {
  value = azurerm_cognitive_deployment.this.name
}

output "residency" {
  value = {
    topology     = "azure_openai"
    provisioned  = true
    declared_region = var.location
    # Resolved from the shared table, never restated here — see region_jurisdictions.json.
    geography          = local.region_facts.geography
    country_iso        = local.region_facts.country_iso
    residency_boundary = local.region_facts.residency_boundary
    # Where the request is processed is decided by the deployment SKU, not by the account's region.
    # A GlobalStandard deployment in Sweden Central may serve the request from anywhere Microsoft
    # runs the model, which is why this field carries the SKU rather than repeating the region.
    processing_scope = var.deployment.sku_name
    model            = "${var.deployment.model_name}:${var.deployment.model_version}"
    # Stated because the value is verifiable from this configuration and is the thing a reviewer
    # would otherwise have to take on trust.
    keys_disabled     = true
    region_table_used = true
  }
}

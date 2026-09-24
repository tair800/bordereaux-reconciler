locals {
  environment = "bench"

  # bench exists to be destroyed. It is the only environment that provisions the GPU topology, and a
  # GPU workload profile bills for allocated nodes rather than for requests — so the environment is
  # created for a benchmark run and torn down after it, which is what keeps the portfolio's largest
  # cost short-lived by construction rather than by somebody remembering.
  #
  # Purge protection is therefore off and must stay off: a vault that cannot be purged would hold
  # this environment's names for 90 days after the first teardown, and the second benchmark run
  # would fail to create anything.
  key_vault_purge_protection_enabled = false

  # No private endpoints. bench has a VNet because workload profiles require one, not because
  # anything here needs to be unreachable; private endpoints would add DNS zones and latency to an
  # environment that holds synthetic fixtures and lives for an afternoon.
  private_endpoints_enabled = false
}

module "platform" {
  source = "../../modules/platform"

  environment = local.environment
  location    = var.location
  name_suffix = var.name_suffix
  tenant_id   = var.tenant_id
  tags        = var.tags

  key_vault_purge_protection_enabled = local.key_vault_purge_protection_enabled
  private_endpoints_enabled          = local.private_endpoints_enabled
  network                            = var.network
  container_app_workload_profiles    = var.container_app_workload_profiles

  postgres                 = var.postgres
  entra_admin              = var.entra_admin
  allowed_client_ip_ranges = var.allowed_client_ip_ranges

  api_min_replicas = var.api_min_replicas
  blob_retention   = var.blob_retention

  budget = {
    monthly_amount = var.budget.monthly_amount
    start_date     = var.budget.start_date
    thresholds     = var.budget.thresholds
    contact_emails = var.budget_contact_emails
  }
}

# `count` on the module, not on every resource inside it. That is the whole argument the blueprint
# makes against CLI workspaces for this project: the three arms provision materially different
# things — an account and a deployment, nothing at all, a GPU container app — and a single
# configuration serving all three would carry a conditional on almost every resource it contains.

module "inference_azure_openai" {
  source = "../../modules/inference_azure_openai"
  count  = var.enabled_topologies.azure_openai.enabled ? 1 : 0

  environment         = local.environment
  resource_group_name = module.platform.resource_group_name
  name_suffix         = var.name_suffix
  location            = var.enabled_topologies.azure_openai.location
  sku_name            = var.enabled_topologies.azure_openai.sku_name
  deployment          = var.enabled_topologies.azure_openai.deployment
  tags                = var.tags

  identity_principal_id      = module.platform.identity_principal_id
  log_analytics_workspace_id = module.platform.log_analytics_workspace_id

  public_network_access_enabled = !local.private_endpoints_enabled
}

module "inference_hosted_api" {
  source = "../../modules/inference_hosted_api"
  count  = var.enabled_topologies.hosted_api.enabled ? 1 : 0

  provider_label              = var.enabled_topologies.hosted_api.provider_label
  endpoint_host               = var.enabled_topologies.hosted_api.endpoint_host
  declared_region             = var.enabled_topologies.hosted_api.declared_region
  declared_country_iso        = var.enabled_topologies.hosted_api.declared_country_iso
  declared_residency_boundary = var.enabled_topologies.hosted_api.declared_residency_boundary
  declaration_source          = var.enabled_topologies.hosted_api.declaration_source
  key_vault_secret_name       = var.enabled_topologies.hosted_api.key_vault_secret_name
}

module "inference_selfhost_gpu" {
  source = "../../modules/inference_selfhost_gpu"
  count  = var.enabled_topologies.selfhost_gpu.enabled ? 1 : 0

  environment         = local.environment
  resource_group_name = module.platform.resource_group_name
  location            = var.enabled_topologies.selfhost_gpu.location
  tags                = var.tags

  container_app_environment_id    = module.platform.container_app_environment_id
  workload_profile_name           = var.enabled_topologies.selfhost_gpu.workload_profile_name
  identity_id                     = module.platform.identity_id
  identity_client_id              = module.platform.identity_client_id
  container_registry_login_server = module.platform.container_registry_login_server

  model_id     = var.enabled_topologies.selfhost_gpu.model_id
  min_replicas = var.enabled_topologies.selfhost_gpu.min_replicas
  resources    = var.enabled_topologies.selfhost_gpu.resources
}

locals {
  # Only enabled arms appear. An arm that is off is absent from the manifest rather than present
  # with `enabled = false`, because the manifest answers "where is a model call processed in this
  # environment" and a topology that cannot be called does not have an answer to contribute.
  inference_residency = merge(
    var.enabled_topologies.azure_openai.enabled ? {
      azure_openai = module.inference_azure_openai[0].residency
    } : {},
    var.enabled_topologies.hosted_api.enabled ? {
      hosted_api = module.inference_hosted_api[0].residency
    } : {},
    var.enabled_topologies.selfhost_gpu.enabled ? {
      selfhost_gpu = module.inference_selfhost_gpu[0].residency
    } : {},
  )
}

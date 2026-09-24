# The residency manifest.
#
# `terraform output -json residency_manifest` produces the object that docs/residency/dev.json
# carries under its "manifest" key. scripts/residency_manifest.py renders the committed copy from
# the same committed inputs without running a plan, which is the only way to have one at all in a
# build with no subscription — and the equality of the two is kill condition H.
#
# The statement is read from a file rather than written here so that three environment roots cannot
# come to disagree about what the manifest does and does not claim.
output "residency_manifest" {
  value = {
    schema_version = 1
    environment    = local.environment
    statement      = file("${path.module}/../../residency_statement.md")
    platform       = module.platform.residency
    inference      = local.inference_residency
  }
}

# Operational outputs. Not part of the manifest — see the note in modules/platform/outputs.tf on
# why resource names are kept out of it.
output "resource_group_name" {
  value = module.platform.resource_group_name
}

output "resource_names" {
  value = module.platform.resource_names
}

output "api_fqdn" {
  value = module.platform.api_fqdn
}

output "container_registry_login_server" {
  value = module.platform.container_registry_login_server
}

output "key_vault_uri" {
  value = module.platform.key_vault_uri
}

output "azure_openai_endpoint" {
  value = one(module.inference_azure_openai[*].endpoint)
}

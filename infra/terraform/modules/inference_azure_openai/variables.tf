variable "environment" {
  type = string
}

variable "resource_group_name" {
  type = string
}

variable "name_suffix" {
  type = string
}

variable "location" {
  type        = string
  description = <<-EOT
    Region for the Azure OpenAI account.

    Deliberately a separate variable from the platform's location. The whole reason this project
    models inference topology as configuration is that the model may sit somewhere the ledger does
    not — an EU data zone deployment serving an application whose database is in the UK is a
    perfectly ordinary arrangement, and a manifest that assumed one region for both would report it
    wrongly.
  EOT

  validation {
    condition = contains(
      keys(jsondecode(file("${path.module}/../../region_jurisdictions.json")).regions),
      var.location
    )
    error_message = "location is not listed in region_jurisdictions.json; add it there first."
  }
}

variable "sku_name" {
  type        = string
  default     = "S0"
  description = "Cognitive account SKU. S0 is the only standard tier for the OpenAI kind."
}

variable "deployment" {
  type = object({
    name          = string
    model_name    = string
    model_version = string
    sku_name      = string
    capacity      = number
  })
  description = <<-EOT
    The model deployment.

    `sku_name` is the load-bearing field for the residency claim, not `capacity`. "DataZoneStandard"
    keeps inference inside a declared data zone; "GlobalStandard" routes to whichever region has
    capacity, anywhere Microsoft operates the model. Two deployments identical in every other respect
    make opposite residency claims depending on this one string, which is exactly why it is in
    version control and in the manifest rather than chosen in the portal.
  EOT

  validation {
    condition = contains(
      ["DataZoneStandard", "Standard", "GlobalStandard", "DataZoneProvisionedManaged"],
      var.deployment.sku_name
    )
    error_message = "deployment.sku_name must be a recognised Azure OpenAI deployment SKU."
  }
}

variable "identity_principal_id" {
  type        = string
  description = "Principal granted Cognitive Services OpenAI User on this account. See main.tf."
}

variable "log_analytics_workspace_id" {
  type = string
}

variable "public_network_access_enabled" {
  type    = bool
  default = true
}

variable "tags" {
  type    = map(string)
  default = {}
}

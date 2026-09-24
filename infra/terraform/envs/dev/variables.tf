# Two kinds of variable live here, and the split is the whole filing system of this root.
#
# **Declared** variables get their values from residency.auto.tfvars.json, which is committed.
# Terraform auto-loads any `*.auto.tfvars.json` in the working directory, and
# scripts/residency_manifest.py reads the same bytes — so the residency manifest can be rendered
# without a subscription, and cannot describe a configuration different from the one that would be
# applied. That property is what kill condition H in DECISIONS.md rests on.
#
# **Operator** variables have no default and are never committed: subscription and tenant ids, the
# database administrator, budget alert recipients, firewall openings. They are supplied through
# TF_VAR_* or a gitignored *.tfvars at plan time. None of them appears in the residency manifest,
# which is why the manifest can be committed at all.

# ----------------------------------------------------------------------------------------------
# Declared — from residency.auto.tfvars.json
# ----------------------------------------------------------------------------------------------

variable "location" {
  type        = string
  description = "Azure region for the platform. Must appear in region_jurisdictions.json."
}

variable "name_suffix" {
  type        = string
  description = "Suffix for globally-unique names. Committed; see the platform module for why."
}

variable "enabled_topologies" {
  type = object({
    azure_openai = object({
      enabled  = bool
      location = string
      sku_name = string
      deployment = object({
        name          = string
        model_name    = string
        model_version = string
        sku_name      = string
        capacity      = number
      })
    })

    hosted_api = object({
      enabled                     = bool
      provider_label              = string
      endpoint_host               = string
      declared_region             = string
      declared_country_iso        = string
      declared_residency_boundary = string
      declaration_source          = string
      key_vault_secret_name       = string
    })

    selfhost_gpu = object({
      enabled               = bool
      location              = string
      workload_profile_name = string
      model_id              = string
      min_replicas          = number
      resources = object({
        cpu    = number
        memory = string
      })
    })
  })

  description = <<-EOT
    Which inference topologies this environment provisions, and everything each one needs.

    A single typed object rather than three booleans and a scattering of loose strings, because the
    three arms provision materially different resource sets and each one's residency claim depends
    on fields only it has. Typing it means a topology cannot be enabled with half its declaration
    missing — Terraform rejects the object before any resource is planned.

    Every arm is present in the type whether or not it is enabled here. An absent arm would make the
    type differ per environment, and a residency manifest whose schema changes per environment
    cannot be compared across them.
  EOT
}

variable "postgres" {
  type = object({
    sku_name   = string
    storage_mb = number
    version    = string
    zone       = string
  })
}

variable "blob_retention" {
  type = object({
    raw_tier_to_cool_days        = number
    raw_tier_to_archive_days     = number
    quarantine_tier_to_cool_days = number
    quarantine_delete_days       = number
    replay_tier_to_cool_days     = number
    replay_delete_days           = number
  })
}

variable "budget" {
  type = object({
    monthly_amount = number
    start_date     = string
    thresholds     = list(number)
  })
  description = "Recipients are an operator variable; only the shape of the budget is committed."
}

variable "network" {
  type = object({
    address_space           = string
    infrastructure_subnet   = string
    postgres_subnet         = string
    private_endpoint_subnet = string
  })
  default     = null
  description = "Null in environments that need neither workload profiles nor private endpoints."
}

variable "container_app_workload_profiles" {
  type = list(object({
    name                  = string
    workload_profile_type = string
    minimum_count         = number
    maximum_count         = number
  }))
  default = []
}

variable "api_min_replicas" {
  type    = number
  default = 0
}

# ----------------------------------------------------------------------------------------------
# Operator — never committed
# ----------------------------------------------------------------------------------------------

variable "subscription_id" {
  type        = string
  description = "Target subscription. Supplied per operator; not a property of the environment."
}

variable "tenant_id" {
  type = string
}

variable "entra_admin" {
  type = object({
    object_id      = string
    principal_name = string
    principal_type = string
  })
  description = "The Entra principal that administers the database. There is no password fallback."
}

variable "budget_contact_emails" {
  type        = list(string)
  default     = []
  description = <<-EOT
    Where budget alerts go.

    Uncommitted because it is personal data, and because a repository that ships somebody's address
    in a JSON file will eventually ship it somewhere that address did not expect to be.
  EOT
}

variable "allowed_client_ip_ranges" {
  type = map(object({
    start_ip = string
    end_ip   = string
  }))
  default     = {}
  description = "PostgreSQL firewall openings, keyed by who asked for them. Empty means closed."
}

variable "tags" {
  type    = map(string)
  default = {}
}

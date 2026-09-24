variable "environment" {
  type        = string
  description = "Environment name. Appears in every resource name and in the residency manifest."

  validation {
    condition     = contains(["dev", "bench", "prod"], var.environment)
    error_message = "environment must be one of dev, bench, prod — the three committed env roots."
  }
}

variable "location" {
  type        = string
  description = <<-EOT
    Azure region for every resource this module creates.

    This is the value the residency claim rests on, so it is constrained to regions present in
    region_jurisdictions.json rather than left free. A typo that lands the ledger in the wrong
    geography should fail at plan, not be discovered in an audit.
  EOT

  validation {
    condition = contains(
      keys(jsondecode(file("${path.module}/../../region_jurisdictions.json")).regions),
      var.location
    )
    error_message = "location is not listed in region_jurisdictions.json; add it there first."
  }
}

variable "name_suffix" {
  type        = string
  description = <<-EOT
    Short lowercase suffix making globally-unique names unique.

    Committed rather than generated. `random_string` would put a value in state that a fresh clone
    of this repository cannot reproduce, which means the names in the residency manifest would stop
    being derivable from the configuration alone — and a manifest that needs the state file to be
    checkable is not much of a manifest.
  EOT

  validation {
    condition     = can(regex("^[a-z0-9]{3,6}$", var.name_suffix))
    error_message = "name_suffix must be 3-6 lowercase alphanumeric characters."
  }
}

variable "tenant_id" {
  type        = string
  description = "Entra tenant. Not committed — supplied per operator at plan time."
}

variable "tags" {
  type        = map(string)
  default     = {}
  description = "Tags applied to every resource that accepts them."
}

variable "log_retention_days" {
  type        = number
  default     = 30
  description = "Log Analytics retention. 30 is the free floor; longer costs money per GB per month."
}

variable "log_daily_quota_gb" {
  type        = number
  default     = 1
  description = <<-EOT
    Hard daily ingestion cap on the workspace, in GB.

    The provider default is unlimited, and unlimited ingestion on a workspace collecting diagnostic
    logs from every data resource is the single most effective way to turn a demo environment into a
    four-figure invoice. Capping it means losing telemetry on a bad day, which is the correct trade
    for an environment nobody is paged for.
  EOT
}

variable "key_vault_purge_protection_enabled" {
  type        = bool
  description = <<-EOT
    Key Vault purge protection. True in prod, false everywhere else.

    Irreversible: once enabled on a vault it cannot be disabled, and a soft-deleted vault then holds
    its name for 90 days against any attempt to recreate it. In a non-prod environment that is
    created and destroyed repeatedly, that is a 90-day outage of your own naming scheme.
  EOT
}

variable "private_endpoints_enabled" {
  type        = bool
  default     = false
  description = "Private endpoints for blob, vault and registry. Prod only; needs var.network."
}

variable "network" {
  type = object({
    address_space            = string
    infrastructure_subnet    = string
    postgres_subnet          = string
    private_endpoint_subnet  = string
  })
  default     = null
  description = <<-EOT
    VNet and subnet prefixes, or null for a network-free environment.

    Not every environment needs one. A Consumption-only Container App Environment and a
    publicly-reachable PostgreSQL server work without a VNet and cost nothing to not have. A VNet
    becomes mandatory for two reasons only: workload profiles (which the GPU topology needs) and
    private endpoints (which prod needs). Environments that need neither pass null.
  EOT
}

variable "container_app_workload_profiles" {
  type = list(object({
    name                  = string
    workload_profile_type = string
    minimum_count         = number
    maximum_count         = number
  }))
  default     = []
  description = <<-EOT
    Workload profiles on the Container App Environment. Empty means a Consumption-only environment.

    A non-empty list requires var.network, because a workload-profile environment must be injected
    into a delegated subnet. That coupling is enforced in a precondition rather than left to be
    discovered from an Azure error message.
  EOT
}

variable "postgres" {
  type = object({
    sku_name   = string
    storage_mb = number
    version    = string
    zone       = string
  })
  description = "PostgreSQL Flexible Server sizing. Burstable tiers are fine for everything but prod."
}

variable "entra_admin" {
  type = object({
    object_id      = string
    principal_name = string
    principal_type = string
  })
  description = <<-EOT
    The Entra principal that administers the database.

    There is no password administrator to fall back on — password_auth_enabled is false — so if this
    is wrong, nobody can reach the database at all. That is the intended failure mode: a database
    with no password cannot leak one.
  EOT
}

variable "allowed_client_ip_ranges" {
  type = map(object({
    start_ip = string
    end_ip   = string
  }))
  default     = {}
  description = <<-EOT
    Firewall openings on a publicly-reachable PostgreSQL server, keyed by a human-readable name.

    Empty by default, which means the server is reachable by nothing. Filling this in is a deliberate
    act with a name attached to each range, so `terraform plan` shows who opened what.
  EOT
}

variable "api_min_replicas" {
  type        = number
  default     = 0
  description = <<-EOT
    Minimum replicas for the API container app.

    Zero by default so an idle environment bills nothing for compute. The first request after an idle
    period pays a cold start; for a reconciliation service whose traffic is a handful of uploads a
    day, that is a trade worth making everywhere except prod.
  EOT
}

variable "container_image" {
  type        = string
  default     = "mcr.microsoft.com/k8se/quickstart:latest"
  description = <<-EOT
    Placeholder image. Terraform provisions the app; it does not decide which build runs in it.

    Image tags are a deployment concern and change several times a day, whereas this configuration
    changes a few times a quarter. Putting the tag here would mean every deployment is a Terraform
    apply and every drift check is a false positive — so the container resources carry
    `ignore_changes` on the image and the deployment pipeline owns it.
  EOT
}

variable "blob_retention" {
  type = object({
    raw_tier_to_cool_days       = number
    raw_tier_to_archive_days    = number
    quarantine_tier_to_cool_days = number
    quarantine_delete_days      = number
    replay_tier_to_cool_days    = number
    replay_delete_days          = number
  })
  description = <<-EOT
    Blob lifecycle thresholds in days.

    These are defaults, not advice. How long a quarantined coverholder file may be kept is a
    retention decision for whoever owns the DPIA — the numbers committed in the env declarations are
    a starting position chosen so the policy is visible and arguable, not so it is correct for
    somebody else's register.
  EOT
}

variable "budget" {
  type = object({
    monthly_amount = number
    start_date     = string
    contact_emails = list(string)
    thresholds     = list(number)
  })
  description = <<-EOT
    Consumption budget for this environment's resource group.

    start_date must be the first of a month and, at creation time, not in the past — Azure rejects
    anything else. It is a committed constant rather than `timestamp()` for the reason explained at
    the resource itself.
  EOT
}

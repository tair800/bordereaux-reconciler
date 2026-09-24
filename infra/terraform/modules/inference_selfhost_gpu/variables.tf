variable "environment" {
  type = string
}

variable "resource_group_name" {
  type = string
}

variable "location" {
  type        = string
  description = "Region the GPU replica runs in. Same table as everything else."

  validation {
    condition = contains(
      keys(jsondecode(file("${path.module}/../../region_jurisdictions.json")).regions),
      var.location
    )
    error_message = "location is not listed in region_jurisdictions.json; add it there first."
  }
}

variable "container_app_environment_id" {
  type = string
}

variable "workload_profile_name" {
  type        = string
  description = <<-EOT
    Name of the GPU workload profile on the Container App Environment.

    The profile itself is created by the platform module, because it is a property of the
    environment rather than of the app. This module only places an app onto it, which is why
    enabling this topology also requires the platform module to be given a matching entry in
    container_app_workload_profiles — and a VNet, since workload-profile environments are
    VNet-injected.
  EOT
}

variable "identity_id" {
  type = string
}

variable "identity_client_id" {
  type = string
}

variable "container_registry_login_server" {
  type = string
}

variable "image" {
  type        = string
  default     = "mcr.microsoft.com/k8se/quickstart:latest"
  description = "Placeholder. The serving image tag is the deployment pipeline's, as everywhere else."
}

variable "resources" {
  type = object({
    cpu    = number
    memory = string
  })
  description = <<-EOT
    CPU and memory requested by the replica.

    These must match what the chosen GPU workload profile actually allocates — Container Apps
    rejects a request the profile cannot satisfy, and the allocatable figures differ per profile
    type and per region. **This has not been checked against a live subscription** (this build has
    none), so treat the committed defaults as a starting point to confirm with
    `az containerapp env workload-profile list-supported` before the first apply.
  EOT
}

variable "model_id" {
  type        = string
  description = "Open-weight model served by this replica, recorded in the residency manifest."
}

variable "min_replicas" {
  type        = number
  default     = 0
  description = <<-EOT
    Zero by default, and the default matters here more than anywhere else in this configuration.

    A GPU workload profile bills for allocated nodes, not for requests. A self-hosted arm left at
    one replica is the largest standing cost in the whole portfolio, which is why this topology is
    enabled only in the bench environment and why that environment is destroyed after a run.
  EOT
}

variable "tags" {
  type    = map(string)
  default = {}
}

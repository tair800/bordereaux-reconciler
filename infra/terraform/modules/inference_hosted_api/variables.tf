# A module that provisions nothing.
#
# The hosted-API topology is a third party's endpoint reached over the internet. There is no Azure
# resource to create for it — which is precisely the reason the blueprint rejected CLI workspaces
# for this project: one configuration serving all three topologies would need a conditional `count`
# on nearly every resource to express "this arm provisions nothing", and that is the workspace
# anti-pattern in its most literal form.
#
# What this module does instead is make the arm's residency claim as structurally checkable as the
# Azure one. For an Azure region, jurisdiction is resolved from the shared table and the declaration
# cannot be wrong. For a third party it cannot be resolved from anything — nobody outside the vendor
# can verify where a request is processed — so the declaration is carried through to the manifest
# marked as unverified, with the source of the claim named. An unverifiable fact reported as
# unverified is useful. The same fact reported as if it had been checked is worse than silence.

variable "provider_label" {
  type        = string
  description = "Short identifier for the hosted API arm, as it appears in the residency manifest."
}

variable "endpoint_host" {
  type        = string
  description = "Hostname the application calls. Recorded so the manifest names what it is describing."
}

variable "declared_region" {
  type        = string
  description = "The region the operator declares this endpoint serves from."
}

variable "declared_country_iso" {
  type        = string
  description = "ISO 3166-1 alpha-2 country for declared_region."

  validation {
    condition     = can(regex("^[A-Z]{2}$", var.declared_country_iso))
    error_message = "declared_country_iso must be a two-letter uppercase ISO 3166-1 alpha-2 code."
  }
}

variable "declared_residency_boundary" {
  type        = string
  description = "Residency boundary the operator declares, e.g. \"US\" or \"EU/EEA\"."

  validation {
    condition     = trimspace(var.declared_residency_boundary) != ""
    error_message = "declared_residency_boundary must not be empty; an unstated boundary is not a claim."
  }
}

variable "declaration_source" {
  type        = string
  description = <<-EOT
    Where the residency declaration above came from — a vendor document, a contract clause, a DPA.

    Required, with no default. The manifest has to be able to say who is making the claim, because
    the answer for this topology is never "Terraform verified it".
  EOT

  validation {
    condition     = trimspace(var.declaration_source) != ""
    error_message = "declaration_source must name the document or party asserting the residency claim."
  }
}

variable "key_vault_secret_name" {
  type        = string
  description = <<-EOT
    Name of the Key Vault secret holding this provider's API key.

    The name, not the value. The vault and the application's read access to it are provisioned by
    the platform module; putting the key itself in Terraform would write it to state in plaintext.
  EOT
}

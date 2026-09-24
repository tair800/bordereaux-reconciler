terraform {
  # The CLI this configuration was written and validated against is 1.16.4. `~>` on a two-part
  # version allows 1.16.x and refuses 1.17, which is the right strictness for a state file that
  # several people and a pipeline share: a newer CLI writes a newer state format and the older one
  # can then no longer read it.
  required_version = "~> 1.16"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 5.3"
    }
  }

  # Deliberately partial. The state storage account's name carries an operator-specific suffix, so
  # committing it here would be committing a value that is wrong for everyone but its author; the
  # remaining settings are supplied by `terraform init -backend-config=...`, which
  # infra/bootstrap/README.md spells out.
  #
  # Locking needs nothing else: the azurerm backend takes a native lease on the state blob for the
  # duration of an operation. There is no lock table, no Terraform Cloud workspace and no second
  # service to keep alive.
  #
  # This backend has never been initialised. `terraform init -backend=false` is what has been run
  # here — see infra/README.md.
  backend "azurerm" {
    use_azuread_auth = true
  }
}

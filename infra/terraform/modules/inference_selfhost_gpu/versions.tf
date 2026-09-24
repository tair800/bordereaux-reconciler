terraform {
  required_version = "~> 1.16"

  required_providers {
    azurerm = {
      source = "hashicorp/azurerm"
      # Modules constrain, roots pin. The exact version selected for every root is recorded in
      # that root's .terraform.lock.hcl, which is committed; a module that pinned a patch version
      # here would only be able to disagree with it.
      version = "~> 5.3"
    }
  }
}

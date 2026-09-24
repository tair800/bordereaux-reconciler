terraform {
  required_version = "~> 1.16"

  # No `required_providers`. This module creates no Azure resources, and declaring a provider it
  # never uses would make it look like it does. See variables.tf for why a module that provisions
  # nothing is still worth having.
}

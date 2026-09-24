resource "azurerm_storage_account" "this" {
  name                = local.names.storage_account
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location

  account_tier             = "Standard"
  account_kind             = "StorageV2"
  account_replication_type = var.environment == "prod" ? "ZRS" : "LRS"
  tags                     = local.tags

  # No account keys. The application reaches blob storage as the managed identity and nothing else
  # can reach it at all, which removes the credential that storage incidents are usually about.
  #
  # This setting has a consequence for Terraform itself: with shared keys off, the provider cannot
  # use the Blob data-plane API to manage containers. That is why the containers below are declared
  # with `storage_account_id` — the azurerm 5.x form, which goes through the ARM management plane
  # and authenticates as the Terraform principal. The 3.x `storage_account_name` form issued a
  # data-plane call and would return 403 against this account.
  shared_access_key_enabled = false

  https_traffic_only_enabled = true
  min_tls_version            = "TLS1_2"

  allow_nested_items_to_be_public = false
  public_network_access_enabled   = !var.private_endpoints_enabled
  default_to_oauth_authentication = true

  blob_properties {
    # Raw coverholder spreadsheets are the evidence behind every cell-level lineage record. If a
    # re-upload could overwrite the bytes a canonical value was derived from, the lineage would
    # point at a file that no longer says what it said — and the idempotency claim would be
    # unverifiable after the fact. Versioning is what makes the source cell permanently quotable.
    versioning_enabled = true

    delete_retention_policy {
      days = 30
    }

    container_delete_retention_policy {
      days = 30
    }
  }

  dynamic "network_rules" {
    for_each = var.private_endpoints_enabled ? [1] : []

    content {
      default_action = "Deny"
      bypass         = ["AzureServices"]
    }
  }
}

locals {
  # Named here so the lifecycle policy, the outputs and any future consumer agree on the set.
  blob_containers = {
    "raw-bordereaux"   = "evidence"
    "quarantine"       = "rejected_input"
    "replay-snapshots" = "derived_reproducible"
  }
}

resource "azurerm_storage_container" "this" {
  for_each = local.blob_containers

  name               = each.key
  storage_account_id = azurerm_storage_account.this.id

  # Private is the provider default; stating it makes the review of this file shorter than the
  # review of whether the default changed.
  container_access_type = "private"

  # The class of data a container holds, readable from Azure without this repository in front of
  # you. It is what decides whether the lifecycle rule below may delete anything.
  metadata = {
    data_class = each.value
  }
}

# Tiering, not deletion, is the default posture here. Cool and archive storage cost a fraction of
# hot and the retrieval latency is irrelevant for files nobody reads until somebody disputes a
# figure. Deletion is applied only where a retention decision has been taken deliberately — see the
# blob_retention variable, which says plainly that these numbers are a starting position.
resource "azurerm_storage_management_policy" "this" {
  storage_account_id = azurerm_storage_account.this.id

  rule {
    name    = "raw-bordereaux-tiering"
    enabled = true

    filters {
      prefix_match = ["raw-bordereaux/"]
      blob_types   = ["blockBlob"]
    }

    actions {
      base_blob {
        tier_to_cool_after_days_since_modification_greater_than    = var.blob_retention.raw_tier_to_cool_days
        tier_to_archive_after_days_since_modification_greater_than = var.blob_retention.raw_tier_to_archive_days
      }

      # Superseded versions exist because a coverholder re-sent a corrected file. They are still
      # evidence — the reconciliation that ran against the old bytes happened — so they are tiered
      # down rather than removed. No delete action appears in this rule at all, deliberately.
      version {
        tier_to_cold_after_days_since_creation_greater_than = var.blob_retention.raw_tier_to_cool_days
      }
    }
  }

  rule {
    name    = "quarantine-retention"
    enabled = true

    filters {
      prefix_match = ["quarantine/"]
      blob_types   = ["blockBlob"]
    }

    actions {
      base_blob {
        tier_to_cool_after_days_since_modification_greater_than = var.blob_retention.quarantine_tier_to_cool_days
        # Quarantined files contain personal data that was never accepted into the ledger, so
        # keeping them forever is the harder position to defend of the two. The period is a
        # declared decision, not a default that happened.
        delete_after_days_since_modification_greater_than = var.blob_retention.quarantine_delete_days
      }
    }
  }

  rule {
    name    = "replay-snapshot-retention"
    enabled = true

    filters {
      prefix_match = ["replay-snapshots/"]
      blob_types   = ["blockBlob"]
    }

    actions {
      base_blob {
        tier_to_cool_after_days_since_modification_greater_than = var.blob_retention.replay_tier_to_cool_days
        # Snapshots are reproducible from raw-bordereaux by construction, so they are the one thing
        # here that can be deleted without losing information.
        delete_after_days_since_modification_greater_than = var.blob_retention.replay_delete_days
      }
    }
  }
}

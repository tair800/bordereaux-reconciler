provider "azurerm" {
  subscription_id = var.subscription_id
  tenant_id       = var.tenant_id

  features {
    # Load-bearing, not decorative.
    #
    # Destroying an Azure OpenAI account leaves a soft-deleted account that keeps its name reserved
    # at tenant scope, and the portal does not list it unless you go looking. The next apply then
    # fails on a name conflict with something that appears not to exist. Without this flag a
    # configuration provisions Azure OpenAI successfully exactly once, which is the state most
    # portfolio Terraform is in — it worked on the day it was written and has never been re-run.
    #
    # This project's bench environment is destroyed after every benchmark run, so it would meet the
    # problem on its second use rather than eventually.
    cognitive_account {
      purge_soft_delete_on_destroy = true
    }

    key_vault {
      # Same shape of problem, same answer: a soft-deleted vault holds its name for the retention
      # period. Purging on destroy is safe here because purge protection is off outside prod; in
      # prod the vault refuses to be purged at all, which is the point of enabling it there.
      purge_soft_delete_on_destroy          = true
      purge_soft_deleted_secrets_on_destroy = true
      recover_soft_deleted_key_vaults       = true
    }

    resource_group {
      # The default is to delete a resource group even if something outside this configuration has
      # been created inside it. In an environment where an engineer may have added a resource by
      # hand to debug something, that silently destroys their work.
      prevent_deletion_if_contains_resources = true
    }

    storage {
      # The application storage account has shared_access_key_enabled = false, so the provider must
      # not reach for the Blob data-plane API. Leaving this on produces 403s on operations that look
      # like they should be management-plane.
      data_plane_available = false
    }
  }
}

resource "azurerm_container_registry" "this" {
  name                = local.names.container_registry
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location

  # Premium is the only SKU that supports private endpoints, so prod pays for it and nothing else
  # does. This is the one place in the configuration where a security control forces a SKU change.
  sku = var.private_endpoints_enabled ? "Premium" : "Basic"

  # The admin account is a username and password that every pull would share. The application pulls
  # as its managed identity (see the AcrPull assignment in rbac.tf) and CI pushes with a federated
  # credential, so the admin account has no remaining use except to be found in a pipeline log.
  admin_enabled = false

  public_network_access_enabled = !var.private_endpoints_enabled
  tags                          = local.tags
}

resource "azurerm_container_app_environment" "this" {
  name                = local.names.container_app_env
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  tags                = local.tags

  log_analytics_workspace_id = azurerm_log_analytics_workspace.this.id
  logs_destination           = "log-analytics"

  # Injected whenever the environment has a VNet at all, not only when it has workload profiles.
  # Workload profiles are one reason to need injection; private endpoints are the other, and they
  # are the one that is easy to get wrong — an environment behind private endpoints whose apps sit
  # outside the VNet cannot resolve or reach the storage account it is supposed to be protecting,
  # and the symptom is a timeout rather than a permission error.
  infrastructure_subnet_id = var.network != null ? azurerm_subnet.infrastructure[0].id : null

  dynamic "workload_profile" {
    for_each = { for profile in var.container_app_workload_profiles : profile.name => profile }

    content {
      name                  = workload_profile.value.name
      workload_profile_type = workload_profile.value.workload_profile_type
      minimum_count         = workload_profile.value.minimum_count
      maximum_count         = workload_profile.value.maximum_count
    }
  }
}

locals {
  # Configuration the API and the worker both need. Endpoints and names only — no credential
  # appears here, because every one of these is reached as the managed identity.
  app_environment = [
    { name = "BRDX_ENVIRONMENT", value = var.environment },
    { name = "BRDX_AZURE_REGION", value = var.location },
    { name = "BRDX_RESIDENCY_BOUNDARY", value = local.region_facts.residency_boundary },
    { name = "BRDX_STORAGE_ACCOUNT", value = azurerm_storage_account.this.name },
    { name = "BRDX_BLOB_ENDPOINT", value = azurerm_storage_account.this.primary_blob_endpoint },
    { name = "BRDX_RAW_CONTAINER", value = "raw-bordereaux" },
    { name = "BRDX_QUARANTINE_CONTAINER", value = "quarantine" },
    { name = "BRDX_REPLAY_CONTAINER", value = "replay-snapshots" },
    { name = "BRDX_KEY_VAULT_URI", value = azurerm_key_vault.this.vault_uri },
    { name = "BRDX_POSTGRES_HOST", value = azurerm_postgresql_flexible_server.this.fqdn },
    { name = "BRDX_POSTGRES_DATABASE", value = azurerm_postgresql_flexible_server_database.ledger.name },
    { name = "BRDX_MANAGED_IDENTITY_CLIENT_ID", value = azurerm_user_assigned_identity.app.client_id },
  ]

  # The running application reads the same region and boundary that the residency manifest publishes,
  # from the same module. That is what lets a test assert the manifest the application reports at
  # runtime equals the one committed under docs/residency/ — kill condition H in DECISIONS.md.
  identity_ids = [azurerm_user_assigned_identity.app.id]
}

resource "azurerm_container_app" "api" {
  name                         = "ca-${local.base}-api"
  resource_group_name          = azurerm_resource_group.this.name
  container_app_environment_id = azurerm_container_app_environment.this.id
  revision_mode                = "Single"
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = local.identity_ids
  }

  registry {
    server   = azurerm_container_registry.this.login_server
    identity = azurerm_user_assigned_identity.app.id
  }

  ingress {
    external_enabled = true
    target_port      = 8000
    transport        = "auto"

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = var.api_min_replicas
    max_replicas = 5

    container {
      name   = "api"
      image  = var.container_image
      cpu    = 0.5
      memory = "1Gi"

      dynamic "env" {
        for_each = local.app_environment

        content {
          name  = env.value.name
          value = env.value.value
        }
      }

      readiness_probe {
        transport = "HTTP"
        port      = 8000
        path      = "/healthz"
      }

      liveness_probe {
        transport     = "HTTP"
        port          = 8000
        path          = "/healthz"
        initial_delay = 10
      }
    }

    http_scale_rule {
      name                = "http"
      concurrent_requests = "20"
    }
  }

  lifecycle {
    # The image tag is the deployment pipeline's, not Terraform's. Without this, every deploy makes
    # the next `terraform plan` propose rolling the app back to the placeholder.
    ignore_changes = [template[0].container[0].image]
  }
}

resource "azurerm_container_app" "worker" {
  name                         = "ca-${local.base}-worker"
  resource_group_name          = azurerm_resource_group.this.name
  container_app_environment_id = azurerm_container_app_environment.this.id
  revision_mode                = "Single"
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = local.identity_ids
  }

  registry {
    server   = azurerm_container_registry.this.login_server
    identity = azurerm_user_assigned_identity.app.id
  }

  # No ingress. The worker pulls reconciliation jobs; nothing calls it. Scale-to-zero is off because
  # a worker that sleeps is a queue that grows — unlike the API, its cold start is not paid by a
  # waiting human.
  template {
    min_replicas = 1
    max_replicas = 3

    container {
      name   = "worker"
      image  = var.container_image
      cpu    = 1.0
      memory = "2Gi"

      command = ["python", "-m", "bordereaux_reconciler.worker"]

      dynamic "env" {
        for_each = local.app_environment

        content {
          name  = env.value.name
          value = env.value.value
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].container[0].image]
  }
}

# Migrations are a job, not a container start-up step. Running Alembic in the API's entrypoint means
# every replica races to migrate on every scale-out, and a failed migration presents as a crash loop
# rather than as a failed job somebody can read the logs of.
resource "azurerm_container_app_job" "migrate" {
  name                         = "caj-${local.base}-migrate"
  resource_group_name          = azurerm_resource_group.this.name
  location                     = azurerm_resource_group.this.location
  container_app_environment_id = azurerm_container_app_environment.this.id
  tags                         = local.tags

  replica_timeout_in_seconds = 900
  replica_retry_limit        = 0

  identity {
    type         = "UserAssigned"
    identity_ids = local.identity_ids
  }

  registry {
    server   = azurerm_container_registry.this.login_server
    identity = azurerm_user_assigned_identity.app.id
  }

  # Manual: the deployment pipeline starts it and waits for it, between pushing an image and
  # shifting traffic. A schedule would run migrations at a time nobody is watching.
  manual_trigger_config {
    parallelism              = 1
    replica_completion_count = 1
  }

  template {
    container {
      name   = "migrate"
      image  = var.container_image
      cpu    = 0.5
      memory = "1Gi"

      command = ["alembic", "upgrade", "head"]

      dynamic "env" {
        for_each = local.app_environment

        content {
          name  = env.value.name
          value = env.value.value
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].container[0].image]
  }
}

# Nightly replay re-runs a closed period under the current mapping version and compares the result
# with what is in the ledger. It is the scheduled form of the idempotency claim: if re-ingestion can
# change a canonical value, this job is where it shows up, unattended, the night it starts happening.
resource "azurerm_container_app_job" "replay" {
  name                         = "caj-${local.base}-replay"
  resource_group_name          = azurerm_resource_group.this.name
  location                     = azurerm_resource_group.this.location
  container_app_environment_id = azurerm_container_app_environment.this.id
  tags                         = local.tags

  replica_timeout_in_seconds = 3600
  replica_retry_limit        = 1

  identity {
    type         = "UserAssigned"
    identity_ids = local.identity_ids
  }

  registry {
    server   = azurerm_container_registry.this.login_server
    identity = azurerm_user_assigned_identity.app.id
  }

  schedule_trigger_config {
    # 02:15 UTC. Cron here is UTC regardless of the region's local time, which is worth stating
    # because a replay that runs during a European business day competes with ingestion for the
    # database it is verifying.
    cron_expression          = "15 2 * * *"
    parallelism              = 1
    replica_completion_count = 1
  }

  template {
    container {
      name   = "replay"
      image  = var.container_image
      cpu    = 1.0
      memory = "2Gi"

      command = ["python", "-m", "bordereaux_reconciler.replay", "--verify-only"]

      dynamic "env" {
        for_each = local.app_environment

        content {
          name  = env.value.name
          value = env.value.value
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].container[0].image]
  }
}

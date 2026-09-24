locals {
  region_facts = jsondecode(
    file("${path.module}/../../region_jurisdictions.json")
  ).regions[var.location]
}

resource "azurerm_container_app" "inference" {
  name                         = "ca-brdx-${var.environment}-infer"
  resource_group_name          = var.resource_group_name
  container_app_environment_id = var.container_app_environment_id
  workload_profile_name        = var.workload_profile_name
  revision_mode                = "Single"
  tags                         = var.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [var.identity_id]
  }

  registry {
    server   = var.container_registry_login_server
    identity = var.identity_id
  }

  # Internal ingress only. This arm exists so that a mapping proposal can be produced without the
  # request leaving infrastructure this configuration provisions; an externally reachable inference
  # endpoint would hand that property away and add an open door to a GPU.
  ingress {
    external_enabled = false
    target_port      = 8000
    transport        = "http"

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = var.min_replicas
    max_replicas = 1

    container {
      name   = "inference"
      image  = var.image
      cpu    = var.resources.cpu
      memory = var.resources.memory

      env {
        name  = "BRDX_MODEL_ID"
        value = var.model_id
      }

      env {
        name  = "BRDX_INFERENCE_REGION"
        value = var.location
      }

      env {
        name  = "AZURE_CLIENT_ID"
        value = var.identity_client_id
      }

      # A model server that has not finished loading weights answers slowly rather than failing, so
      # a readiness probe with a short initial delay would mark a healthy replica broken during a
      # cold start that legitimately takes minutes.
      readiness_probe {
        transport               = "HTTP"
        port                    = 8000
        path                    = "/health"
        initial_delay           = 120
        failure_count_threshold = 30
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].container[0].image]
  }
}

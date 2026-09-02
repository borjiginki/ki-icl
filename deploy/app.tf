// The Container Apps environment and the app itself.

resource "azurerm_container_app_environment" "this" {
  name                = "${var.name_prefix}-env"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name

  log_analytics_workspace_id = azurerm_log_analytics_workspace.this.id

  # The two lines that make this private. `internal_load_balancer_enabled` gives the
  # environment a private IP instead of a public one, and requires the subnet.
  infrastructure_subnet_id       = azurerm_subnet.infrastructure.id
  internal_load_balancer_enabled = true

  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
  }

  tags = var.tags
}

resource "azurerm_container_app" "this" {
  name                         = "${var.name_prefix}-context"
  container_app_environment_id = azurerm_container_app_environment.this.id
  resource_group_name          = azurerm_resource_group.this.name
  revision_mode                = "Single"
  workload_profile_name        = "Consumption"
  tags                         = var.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.app.id]
  }

  # Pulls with the managed identity. Omitted entirely while running the public
  # quickstart image, which needs no registry at all.
  dynamic "registry" {
    for_each = local.serving_ki_icl ? [1] : []
    content {
      server   = azurerm_container_registry.this.login_server
      identity = azurerm_user_assigned_identity.app.id
    }
  }

  # Referenced by a VERSIONLESS URI, built as a string rather than read through a data
  # source. Two consequences, both wanted: the secret can be created after this applies
  # (no chicken and egg), and rotating it in the vault takes effect on the next revision
  # without a Terraform run. The value never enters Terraform state.
  dynamic "secret" {
    for_each = var.audit_key_enabled ? [1] : []
    content {
      name                = "audit-key"
      key_vault_secret_id = "${azurerm_key_vault.this.vault_uri}secrets/${var.audit_key_secret_name}"
      identity            = azurerm_user_assigned_identity.app.id
    }
  }

  ingress {
    # The security boundary of this whole deployment. Flipping it to true puts the
    # corpus on the internet with Entra token validation as the only thing in front of
    # it, so it should never change in the same commit as anything else.
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
    max_replicas = var.max_replicas

    container {
      name   = "context"
      image  = local.image
      cpu    = 0.25
      memory = "0.5Gi"

      # A TCP probe, because there is no health endpoint to hit: FastMCP exposes no
      # health or ping route, and /mcp answers only POST with a session handshake. A
      # listening socket is the honest extent of what can be checked from outside.
      readiness_probe {
        transport = "TCP"
        port      = 8000
      }

      liveness_probe {
        transport        = "TCP"
        port             = 8000
        initial_delay    = 5
        interval_seconds = 30
      }

      # Everything below is only meaningful for the real image. The quickstart container
      # ignores it, which is harmless and keeps the first apply and the second one
      # structurally identical.

      env {
        name  = "KI_ICL_AUTH"
        value = var.auth_mode
      }

      env {
        name  = "KI_ICL_ENFORCE"
        value = var.enforce_grants ? "enforce" : "observe"
      }

      # Set in the image too, and repeated here so the deployment is readable on its own:
      # somebody looking at the app in the portal should not have to inspect a layer to
      # learn where it listens or where its corpus is.
      env {
        name  = "KI_ICL_HOST"
        value = "0.0.0.0"
      }

      env {
        name  = "KI_ICL_PORT"
        value = "8000"
      }

      env {
        name  = "CONTEXT_ROOT"
        value = "/app/context"
      }

      # Empty: stderr is the only sink, and Container Apps forwards it to the workspace
      # whose retention is the retention policy.
      env {
        name  = "CONTEXT_USAGE_LOG"
        value = ""
      }

      dynamic "env" {
        for_each = var.audit_key_enabled ? [1] : []
        content {
          name        = "KI_ICL_AUDIT_KEY"
          secret_name = "audit-key"
        }
      }

      # Declares that logging is a required control, so the server refuses to start
      # unlogged rather than quietly serving real content with no audit trail. Only
      # meaningful once there are real identities to log.
      dynamic "env" {
        for_each = var.auth_mode == "entra" ? [1] : []
        content {
          name  = "KI_ICL_AUDIT_REQUIRED"
          value = "1"
        }
      }

      dynamic "env" {
        for_each = var.auth_mode == "entra" ? [1] : []
        content {
          name  = "KI_ICL_ENTRA_TENANT_ID"
          value = var.entra.tenant_id
        }
      }

      dynamic "env" {
        for_each = var.auth_mode == "entra" ? [1] : []
        content {
          name  = "KI_ICL_ENTRA_CLIENT_ID"
          value = var.entra.client_id
        }
      }

      dynamic "env" {
        for_each = var.auth_mode == "entra" ? [1] : []
        content {
          name  = "KI_ICL_ENTRA_IDENTIFIER_URI"
          value = local.identifier_uri
        }
      }

      # The public URL clients use, which the server publishes in its RFC 9728
      # protected-resource document. It has to be what a client actually dials, or the
      # OAuth discovery a 401 points at describes a resource nobody is talking to.
      dynamic "env" {
        for_each = var.auth_mode == "entra" ? [1] : []
        content {
          name  = "KI_ICL_ENTRA_BASE_URL"
          value = "https://${var.name_prefix}-context.${azurerm_container_app_environment.this.default_domain}"
        }
      }
    }
  }

  lifecycle {
    # Hard failures, not warnings: each of these would produce a revision that cannot
    # start, or one that serves real content with no audit trail.
    precondition {
      condition     = !(var.audit_key_enabled && !var.create_role_assignments)
      error_message = "audit_key_enabled needs the managed identity to hold Key Vault Secrets User. Either let this configuration create the role assignments, or have somebody grant that role and re-run with create_role_assignments = false only once it exists."
    }

    precondition {
      condition     = var.auth_mode != "entra" || (var.entra.tenant_id != "" && var.entra.client_id != "")
      error_message = "auth_mode = \"entra\" needs entra.tenant_id and entra.client_id. The server exits on boot without them, so this would deploy a revision that never starts."
    }

    precondition {
      condition     = !(var.auth_mode == "entra" && !var.audit_key_enabled)
      error_message = "auth_mode = \"entra\" without audit_key_enabled means real colleagues read real content with no actor recorded, and the server is told logging is a required control (KI_ICL_AUDIT_REQUIRED=1), so it would refuse to start. Set the Key Vault secret and enable it."
    }

    precondition {
      condition     = !(local.serving_ki_icl && !var.create_role_assignments)
      error_message = "Pulling from the registry needs the managed identity to hold AcrPull. Grant it first, then re-run with create_role_assignments = false."
    }
  }

  depends_on = [
    # Not a data dependency, but a real one: a revision that starts before the identity
    # can pull or read fails, and Container Apps does not retry a failed revision on its
    # own once the cause is fixed.
    azurerm_role_assignment.acr_pull,
    azurerm_role_assignment.vault_secrets,
  ]
}

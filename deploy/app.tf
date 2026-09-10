// The Container Apps environment and the app itself.

resource "azurerm_container_app_environment" "this" {
  name                = "${var.name_prefix}-env"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name

  log_analytics_workspace_id = azurerm_log_analytics_workspace.this.id

  # VNet-integrated regardless of ingress, so the environment can still reach the Files
  # private endpoint (see network.tf). internal_load_balancer_enabled used to be true,
  # which turned out to bind the environment to a private-only static IP no matter what
  # the app's own ingress said - var.external_ingress_enabled alone could not reach this
  # from outside kiicl-vnet. Public here; var.external_ingress_enabled on the app's own
  # ingress below is what actually gates exposure, since false there keeps the app
  # internal-only (dapr-to-dapr) even with a public environment.
  infrastructure_subnet_id       = azurerm_subnet.infrastructure.id
  internal_load_balancer_enabled = false

  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
  }

  tags = var.tags
}

# The Files share's access key, taken directly from the storage account resource.
# azurerm_container_app_environment_storage.access_key is a plain attribute with no
# Key-Vault-reference option in this provider version (unlike the audit key's
# `key_vault_secret_id` string, resolved by the platform at runtime), and
# azurerm_storage_account computes its keys into its own state regardless of whether
# anything references them - so routing this through Key Vault first would not have
# kept it out of state either. Going direct removes a Key Vault bootstrap step and its
# RBAC dependency for no change in what's actually exposed. See versions.tf.
resource "azurerm_container_app_environment_storage" "context" {
  name                         = "context-files"
  container_app_environment_id = azurerm_container_app_environment.this.id
  account_name                 = azurerm_storage_account.files.name
  share_name                   = azurerm_storage_share.context.name
  access_key                   = azurerm_storage_account.files.primary_access_key

  # ReadOnly: the server only ever reads its corpus, and this mount is no exception.
  access_mode = "ReadOnly"
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
    # The security boundary of this whole deployment. Public with auth_mode = "off" has
    # no gate but the IP restrictions below, which is why this defaults to internal and
    # needs a deliberate opt-in - see var.external_ingress_enabled.
    external_enabled = var.external_ingress_enabled
    target_port      = 8000
    transport        = "http"

    # Ignored entirely while external_ingress_enabled is false. Once true, Container
    # Apps' ip_security_restriction is deny-by-default the moment any Allow rule
    # exists, so this is the only thing standing between the internet and an
    # unauthenticated corpus - a deliberate, disclosed trade for reaching this from
    # outside kiicl-vnet without a VPN gateway, meant for a short testing window
    # rather than as a permanent posture. Revisit once auth_mode = "entra" is real:
    # at that point the token requirement carries the weight this IP list carries now.
    #
    # An empty list is no rules at all rather than one rule matching nobody, so it
    # lifts the restriction rather than closing it. That is what the allow-list
    # precondition below refuses for the dashboard, and what var.allowed_client_cidrs
    # now says.
    dynamic "ip_security_restriction" {
      for_each = var.external_ingress_enabled ? var.allowed_client_cidrs : []
      content {
        name             = ip_security_restriction.value.name
        action           = "Allow"
        ip_address_range = ip_security_restriction.value.cidr
        description      = ip_security_restriction.value.description
      }
    }

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = var.min_replicas
    max_replicas = var.max_replicas

    # Harmless for the quickstart image, like every env var below: the volume exists
    # from the first apply, mounting an empty share until Stage 2 publishes content to
    # it. See deploy/README.md.
    volume {
      name          = "context-files"
      storage_type  = "AzureFile"
      storage_name  = azurerm_container_app_environment_storage.context.name
      mount_options = "uid=10001,gid=10001,file_mode=0444,dir_mode=0555"
    }

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

      # Mounted at exactly the path CONTEXT_ROOT already points at below, so the corpus
      # arrives with zero code or env-var changes: server/artifacts.py already reads
      # whatever is at CONTEXT_ROOT fresh on every call.
      volume_mounts {
        name = "context-files"
        path = "/app/context"
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
      # whose retention is the retention policy. The dashboard is the one exception, and
      # it is not a second store in the sense that matters: Log Analytics still receives
      # every line, and this file lives in the container's own writable layer, dies with
      # the revision, and is read by nothing but /dashboard.
      env {
        name  = "CONTEXT_USAGE_LOG"
        value = var.dashboard_enabled ? "/app/logs/usage.jsonl" : ""
      }

      # /app is chown'd to the app user in the image, and the read-only corpus mount is
      # at /app/context, so /app/logs is writable by uid 10001. server/usage.py creates
      # it on first write.
      dynamic "env" {
        for_each = var.dashboard_enabled ? [1] : []
        content {
          name  = "KI_ICL_DASHBOARD"
          value = "1"
        }
      }

      # Where the dashboard records what somebody decided about a suggestion, beside
      # the usage log and in the same writable layer. server/dashboard.py resolves this
      # to the same path on its own, and it is set anyway for the reason above: reading
      # the app in the portal should not mean inspecting a layer to learn where a file
      # it writes ends up.
      dynamic "env" {
        for_each = var.dashboard_enabled ? [1] : []
        content {
          name  = "CONTEXT_CURATION"
          value = "/app/logs/curation.json"
        }
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

      # Canonical aliases of the same tenant, client, and public origin.
      # The server prefers these names and still accepts the KI_ICL_ENTRA_* ones.
      dynamic "env" {
        for_each = var.auth_mode == "entra" ? [1] : []
        content {
          name  = "AZURE_TENANT_ID"
          value = var.entra.tenant_id
        }
      }

      dynamic "env" {
        for_each = var.auth_mode == "entra" ? [1] : []
        content {
          name  = "AZURE_CLIENT_ID"
          value = var.entra.client_id
        }
      }

      dynamic "env" {
        for_each = var.auth_mode == "entra" ? [1] : []
        content {
          name  = "MCP_BASE_URL"
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
      condition     = !(local.serving_ki_icl && !var.create_role_assignments && !var.acr_pull_confirmed)
      error_message = "Pulling from the registry needs the managed identity to hold AcrPull. Either let this configuration create it (create_role_assignments = true), or confirm the grant yourself against the real principal id and set acr_pull_confirmed = true."
    }

    precondition {
      condition     = !(var.dashboard_enabled && !var.external_ingress_enabled)
      error_message = "dashboard_enabled without external_ingress_enabled mounts a dashboard nobody can reach: the app's own ingress stays internal-only, and no VPN gateway, ExpressRoute or peering exists anywhere in this configuration."
    }

    precondition {
      condition     = !(var.dashboard_enabled && length(var.allowed_client_cidrs) == 0)
      error_message = "dashboard_enabled with an empty allowed_client_cidrs is an unauthenticated corpus index on the public internet. Container Apps applies no restriction at all when the rule list is empty: deny-by-default begins only once an Allow rule exists, so an empty list opens everyone rather than denying everyone. The list arrives from the ALLOWED_CLIENT_CIDRS secret, so this also fires when that secret is unset, emptied or rotated away while the dashboard stays on."
    }

    precondition {
      condition     = !(var.dashboard_enabled && (var.max_replicas != 1 || var.min_replicas != 1))
      error_message = "dashboard_enabled needs exactly one replica, so both min_replicas and max_replicas have to be 1. Each replica writes its own usage file, so above one the page shows whichever replica the load balancer happened to pick; below one, the file goes with the replica every time the app scales to zero and the page starts its history again. Both are the same failure: numbers that are wrong with nothing on the page saying so. The Deploy workflow passes max_replicas alongside the switch and min_replicas already defaults to 1; a local apply has to set both."
    }

    precondition {
      condition     = !(var.dashboard_enabled && var.auth_mode == "entra")
      error_message = "dashboard_enabled and auth_mode = \"entra\" are mutually exclusive. The dashboard has no authentication of its own and lists every artifact id regardless of grants, so an IP allow-list stops being a defensible gate the moment there are real identities to gate. The server refuses this combination at startup too."
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

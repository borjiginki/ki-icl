// The self-hosted GitHub Actions runner that lets ki-ccl publish to the Files share
// without ever opening its public network access. Persistent rather than ephemeral: a
// long-running Container App that polls GitHub for queued jobs is one moving part
// (a container and its RBAC); an event-triggered alternative would need a second piece
// of infrastructure just to start it (a webhook receiver minting a fresh runner-
// registration token per run), which is more to secure and operate for a job that runs
// a few times a week. The standing cost this trades for that simplicity is a
// registration credential that lives in a running container continuously rather than
// for a few minutes at a time - see runner_github_pat_secret_name below.
//
// Registers repo-scoped, never org-scoped (see var.runner_github_repo), so this
// identity - which can reach the private endpoint in network.tf - can never execute a
// workflow job from any repository but ki-ccl.

resource "azurerm_user_assigned_identity" "runner" {
  name                = "${var.name_prefix}-runner-identity"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  tags                = var.tags
}

# Lets the runner write to the share with no key and no SAS: `az storage file
# upload-batch --auth-mode login` resolves this identity's own token. This is what
# retires the public-access-toggle workaround `make publish` used to need - the runner
# reaches the private endpoint directly, from inside kiicl-vnet, always.
resource "azurerm_role_assignment" "runner_files" {
  count                = var.create_role_assignments ? 1 : 0
  scope                = azurerm_storage_account.files.id
  role_definition_name = "Storage File Data Privileged Contributor"
  principal_id         = azurerm_user_assigned_identity.runner.principal_id
}

# So the runner can read its own GitHub PAT out of the vault at startup, the same
# pattern audit-key uses for the app.
resource "azurerm_role_assignment" "runner_vault_secrets" {
  count                = var.create_role_assignments ? 1 : 0
  scope                = azurerm_key_vault.this.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.runner.principal_id
}

# So the runner can pull its own image, the same pattern acr_pull uses for the app.
resource "azurerm_role_assignment" "runner_acr_pull" {
  count                = var.create_role_assignments ? 1 : 0
  scope                = azurerm_container_registry.this.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.runner.principal_id
}

# Absent entirely until an image exists, rather than running a placeholder: unlike the
# app (where proving the rest of the infrastructure without it is useful), an idle
# runner with nothing to publish has no equivalent first-apply reason to exist.
resource "azurerm_container_app" "runner" {
  count                        = var.runner_image != "" ? 1 : 0
  name                         = "${var.name_prefix}-runner"
  container_app_environment_id = azurerm_container_app_environment.this.id
  resource_group_name          = azurerm_resource_group.this.name
  revision_mode                = "Single"
  workload_profile_name        = "Consumption"
  tags                         = var.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.runner.id]
  }

  registry {
    server   = azurerm_container_registry.this.login_server
    identity = azurerm_user_assigned_identity.runner.id
  }

  # Referenced by a versionless URI for the same reason the app's audit-key secret is:
  # the value can be set (and rotated) after this applies, and rotating it in the vault
  # takes effect on the runner's next restart with no Terraform run.
  secret {
    name                = "github-pat"
    key_vault_secret_id = "${azurerm_key_vault.this.vault_uri}secrets/${var.runner_github_pat_secret_name}"
    identity            = azurerm_user_assigned_identity.runner.id
  }

  # No ingress block at all: this container only ever makes outbound calls (to GitHub,
  # to the Files private endpoint, to Key Vault), so it needs no listener and is not
  # part of the deployment's ingress security boundary discussed in app.tf.
  template {
    min_replicas = 1
    max_replicas = 1

    container {
      name   = "runner"
      image  = var.runner_image
      cpu    = 0.5
      memory = "1Gi"

      env {
        name  = "GITHUB_REPO"
        value = var.runner_github_repo
      }

      env {
        name  = "RUNNER_LABELS"
        value = var.runner_labels
      }

      env {
        name  = "RUNNER_NAME"
        value = "${var.name_prefix}-runner"
      }

      env {
        name        = "GITHUB_PAT"
        secret_name = "github-pat"
      }
    }
  }

  depends_on = [
    azurerm_role_assignment.runner_acr_pull,
    azurerm_role_assignment.runner_vault_secrets,
    azurerm_role_assignment.runner_files,
  ]
}

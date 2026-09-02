// Log Analytics, the registry, the vault, and the identity that ties them together.

# The only store the access log has. The container writes to stderr and nothing else
# (CONTEXT_USAGE_LOG is empty in the image), so this workspace's retention IS the
# retention policy: one store, one setting, one owner. A file sink inside a container
# would be a second store that nobody owns and nothing rotates.
#
# The region matters and is not a preference. Records carry a keyed pseudonym derived
# from a person, which is personal data, so the workspace stays in the EU.
resource "azurerm_log_analytics_workspace" "this" {
  name                = "${var.name_prefix}-logs"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  sku                 = "PerGB2018"
  retention_in_days   = var.log_retention_days
  tags                = var.tags
}

resource "azurerm_container_registry" "this" {
  name                = local.acr_name
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  sku                 = "Basic"

  # No admin user. The app pulls with a managed identity, so a shared username and
  # password would be a credential that exists only to be leaked.
  admin_enabled = false
  tags          = var.tags
}

# Holds the HMAC key for the audit pseudonyms, and nothing else. Not the Entra client
# secret, because there is no Entra client secret: the server is a pure resource server.
resource "azurerm_key_vault" "this" {
  name                = local.vault_name
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  tenant_id           = data.azurerm_client_config.current.tenant_id
  sku_name            = "standard"

  # RBAC rather than access policies: the grant then lives in the same place as every
  # other authorization in this subscription, and shows up in the same reviews.
  rbac_authorization_enabled = true

  # A sandbox has to be genuinely cleanable. Purge protection can never be turned off
  # once enabled, so a playground that enables it leaves an undeletable vault behind.
  purge_protection_enabled   = false
  soft_delete_retention_days = 7

  tags = var.tags
}

data "azurerm_client_config" "current" {}

# One identity for the app, used for two things: pulling the image and reading the key.
# User-assigned rather than system-assigned so the role assignments survive the app
# being recreated, which otherwise means re-granting on every replacement.
resource "azurerm_user_assigned_identity" "app" {
  name                = "${var.name_prefix}-app-identity"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  tags                = var.tags
}

# Both assignments need Microsoft.Authorization/roleAssignments/write, which Contributor
# does not have. If apply fails here, set create_role_assignments = false and have
# somebody with Owner or User Access Administrator grant these two to the identity named
# in the outputs. Nothing else in this configuration depends on them at apply time.
resource "azurerm_role_assignment" "acr_pull" {
  count                = var.create_role_assignments ? 1 : 0
  scope                = azurerm_container_registry.this.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}

resource "azurerm_role_assignment" "vault_secrets" {
  count                = var.create_role_assignments ? 1 : 0
  scope                = azurerm_key_vault.this.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}

# So a human can set the secret value after apply. Without it, an RBAC vault refuses its
# own creator, which is the single most common way this pattern wastes an hour.
resource "azurerm_role_assignment" "vault_admin_for_operator" {
  count                = var.create_role_assignments ? 1 : 0
  scope                = azurerm_key_vault.this.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = data.azurerm_client_config.current.object_id
}

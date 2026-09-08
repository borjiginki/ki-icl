// The Azure Files share holding the domains/ + access-policy.yaml tree, mounted
// read-only into the Container App (see app.tf). Reachable only through the private
// endpoint in network.tf: this account has no public endpoint at all.
//
// An access-table storage account used to live here too, for a future artifact-level
// access-control feature. Removed: out of scope for getting the already-implemented
// server running and testable, and its own private endpoint / role assignment /
// outputs went with it. Revisit from git history if that feature gets picked back up.

resource "azurerm_storage_account" "files" {
  name                     = local.files_account_name
  resource_group_name      = azurerm_resource_group.this.name
  location                 = azurerm_resource_group.this.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
  account_kind             = "StorageV2"
  min_tls_version          = "TLS1_2"

  public_network_access_enabled = false
  tags                          = var.tags
}

resource "azurerm_storage_share" "context" {
  name               = "context"
  storage_account_id = azurerm_storage_account.files.id
  quota              = 1 # GiB, Azure's minimum. The corpus is a few hundred KB.
  enabled_protocol   = "SMB"

  # Not a data dependency Terraform can see on its own, but a real one: creating a
  # share needs actual network reachability to the account, and without this the share
  # creation can race ahead of the private endpoint that provides it.
  depends_on = [azurerm_private_endpoint.files]
}

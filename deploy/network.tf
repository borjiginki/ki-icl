// The VNet the environment lives in.

resource "azurerm_virtual_network" "this" {
  name                = "${var.name_prefix}-vnet"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  address_space       = [var.vnet_address_space]
  tags                = var.tags
}

resource "azurerm_subnet" "infrastructure" {
  name                 = "container-apps-infrastructure"
  resource_group_name  = azurerm_resource_group.this.name
  virtual_network_name = azurerm_virtual_network.this.name
  address_prefixes     = [var.infrastructure_subnet_prefix]

  # Required for a workload-profiles environment. Without it the environment creation
  # fails with a message about the subnet not being delegated, which is not obviously
  # about this line.
  delegation {
    name = "container-apps"
    service_delegation {
      name    = "Microsoft.App/environments"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

# A second subnet, only for the Files private endpoint. The infrastructure subnet above
# is delegated to Microsoft.App/environments, and Azure does not let a delegated subnet
# also host a private endpoint, so this one deliberately carries no delegation at all.
resource "azurerm_subnet" "private_endpoints" {
  name                 = "private-endpoints"
  resource_group_name  = azurerm_resource_group.this.name
  virtual_network_name = azurerm_virtual_network.this.name
  address_prefixes     = ["10.60.2.0/27"]
}

# Confirmed against Microsoft's own Container Apps documentation: traffic to an Azure
# Files mount genuinely traverses the environment's VNet, there is no platform-internal
# bypass. So the storage account's network posture is not incidental, and a private
# endpoint is what lets `public_network_access_enabled = false` on that account (see
# storage.tf) still be reachable from here.
resource "azurerm_private_dns_zone" "files" {
  name                = "privatelink.file.core.windows.net"
  resource_group_name = azurerm_resource_group.this.name
  tags                = var.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "files" {
  name                  = "${var.name_prefix}-files-vnet-link"
  resource_group_name   = azurerm_resource_group.this.name
  private_dns_zone_name = azurerm_private_dns_zone.files.name
  virtual_network_id    = azurerm_virtual_network.this.id
  registration_enabled  = false
  tags                  = var.tags
}

# Judgment call, reversible: a `Microsoft.Storage` service endpoint on the existing
# infrastructure subnet plus a storage firewall rule would also work, and would need no
# new subnet or DNS zone. The private endpoint is chosen instead to match this
# deployment's one existing security boundary ("no public ingress anywhere"), and
# because publishing content from inside the same VNet a person already needs to reach
# the server from - now via the runner in runner.tf, rather than a person on a VPN - is
# a consistent story rather than a second one.
resource "azurerm_private_endpoint" "files" {
  name                = "${var.name_prefix}-files-pe"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  subnet_id           = azurerm_subnet.private_endpoints.id

  private_service_connection {
    name                           = "${var.name_prefix}-files-psc"
    private_connection_resource_id = azurerm_storage_account.files.id
    subresource_names              = ["file"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "files"
    private_dns_zone_ids = [azurerm_private_dns_zone.files.id]
  }

  tags = var.tags
}

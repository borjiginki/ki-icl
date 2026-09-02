// The VNet the environment lives in, and the private DNS that makes an internal
// ingress FQDN resolvable.

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

# An internal environment's FQDN resolves to a private address, and nothing resolves it
# for you: Azure allocates the domain but the zone is yours to create. Without this, a
# client inside the VNet gets NXDOMAIN for an app that is running perfectly well, which
# is a confusing way to spend an afternoon.
resource "azurerm_private_dns_zone" "apps" {
  name                = azurerm_container_app_environment.this.default_domain
  resource_group_name = azurerm_resource_group.this.name
  tags                = var.tags
}

# Wildcard, because the record has to cover every app in the environment, and the app
# names are not known to the zone.
resource "azurerm_private_dns_a_record" "wildcard" {
  name                = "*"
  zone_name           = azurerm_private_dns_zone.apps.name
  resource_group_name = azurerm_resource_group.this.name
  ttl                 = 300
  records             = [azurerm_container_app_environment.this.static_ip_address]
  tags                = var.tags
}

resource "azurerm_private_dns_a_record" "apex" {
  name                = "@"
  zone_name           = azurerm_private_dns_zone.apps.name
  resource_group_name = azurerm_resource_group.this.name
  ttl                 = 300
  records             = [azurerm_container_app_environment.this.static_ip_address]
  tags                = var.tags
}

# Links the zone to this VNet only. Anything else that needs to reach the server, a
# corporate VNet or a VPN gateway's VNet, needs its own link to this same zone. That is
# a deliberate second step: it is the moment somebody decides who can reach the corpus,
# and it should not happen as a side effect of applying this.
resource "azurerm_private_dns_zone_virtual_network_link" "this" {
  name                  = "${var.name_prefix}-vnet-link"
  resource_group_name   = azurerm_resource_group.this.name
  private_dns_zone_name = azurerm_private_dns_zone.apps.name
  virtual_network_id    = azurerm_virtual_network.this.id
  registration_enabled  = false
  tags                  = var.tags
}

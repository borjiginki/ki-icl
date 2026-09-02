output "acr_name" {
  description = "Pass to `az acr build --registry`."
  value       = azurerm_container_registry.this.name
}

output "acr_login_server" {
  description = "Prefix for the `image` variable on the second apply."
  value       = azurerm_container_registry.this.login_server
}

output "mcp_url" {
  description = "The endpoint an MCP client dials. Resolvable only from inside a VNet linked to the private DNS zone, which is the point of internal ingress."
  value       = "https://${azurerm_container_app.this.ingress[0].fqdn}/mcp"
}

output "key_vault_name" {
  description = "Where to set the audit key. See the `audit key` section of deploy/README.md."
  value       = azurerm_key_vault.this.name
}

output "managed_identity_principal_id" {
  description = "Grant AcrPull on the registry and Key Vault Secrets User on the vault to this principal, if create_role_assignments is false."
  value       = azurerm_user_assigned_identity.app.principal_id
}

output "log_analytics_workspace_id" {
  description = "Where the access log lands. Its retention IS the retention policy, because the container writes to stderr and nothing else."
  value       = azurerm_log_analytics_workspace.this.id
}

output "private_dns_zone" {
  description = "Link this zone to any other VNet that needs to reach the server. That link is the moment somebody decides who can read the corpus, so it is deliberately not automatic."
  value       = azurerm_private_dns_zone.apps.name
}

output "what_is_running" {
  description = "Whether this is serving the context layer or Microsoft's placeholder."
  value       = local.serving_ki_icl ? "ki-icl (${var.image}), auth=${var.auth_mode}, grants=${var.enforce_grants ? "enforced" : "observed"}, audit_key=${var.audit_key_enabled ? "wired" : "unset"}" : "Microsoft quickstart placeholder. Infrastructure only: build the image and re-apply with -var image=..."
}

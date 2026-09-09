output "acr_name" {
  description = "Pass to `az acr build --registry`."
  value       = azurerm_container_registry.this.name
}

output "acr_login_server" {
  description = "Prefix for the `image` variable on the second apply."
  value       = azurerm_container_registry.this.login_server
}

output "mcp_url" {
  description = "The endpoint an MCP client dials. Publicly resolvable now that the environment's load balancer is public; var.external_ingress_enabled and the allow-list in var.allowed_client_cidrs are what actually gate who can reach it."
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

output "files_storage_account_name" {
  description = "Set as ki-ccl's FILES_STORAGE_ACCOUNT repo variable - what publish.yml uploads to and this app mounts."
  value       = azurerm_storage_account.files.name
}

output "files_share_name" {
  description = "Set as ki-ccl's FILES_SHARE_NAME repo variable - the share publish.yml uploads to and this app mounts."
  value       = azurerm_storage_share.context.name
}

output "runner_managed_identity_principal_id" {
  description = "Grant Storage File Data Privileged Contributor on the Files account, Key Vault Secrets User on the vault, and AcrPull on the registry to this principal, if create_role_assignments is false."
  value       = azurerm_user_assigned_identity.runner.principal_id
}

output "runner_managed_identity_client_id" {
  description = "Set as ki-ccl's RUNNER_IDENTITY_CLIENT_ID repo variable - what publish.yml's azure/login step authenticates as."
  value       = azurerm_user_assigned_identity.runner.client_id
}

output "runner_azure_tenant_id" {
  description = "Set as ki-ccl's AZURE_TENANT_ID repo variable."
  value       = data.azurerm_client_config.current.tenant_id
}

output "runner_azure_subscription_id" {
  description = "Set as ki-ccl's AZURE_SUBSCRIPTION_ID repo variable."
  value       = var.subscription_id
}

output "what_is_running" {
  description = "Whether this is serving the context layer or Microsoft's placeholder."
  value       = local.serving_ki_icl ? "ki-icl (${var.image}), auth=${var.auth_mode}, grants=${var.enforce_grants ? "enforced" : "observed"}, audit_key=${var.audit_key_enabled ? "wired" : "unset"}" : "Microsoft quickstart placeholder. Infrastructure only: build the image and re-apply with -var image=..."
}

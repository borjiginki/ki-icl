terraform {
  required_version = ">= 1.6"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # State lives in a storage account this configuration deliberately does not manage: the
  # backend cannot be created by the configuration that uses it, so the resource group,
  # storage account and container are bootstrapped once by hand (see deploy/README.md,
  # "Remote state"). Account, container and key name are passed via -backend-config
  # rather than hardcoded here, so this file names no environment-specific value; local
  # runs and CI both supply the same three flags plus an access key, kept out of git and
  # out of this file the same way the audit key is.
  #
  # This is also why it matters: azurerm_storage_account computes its access keys into
  # its own state entry the moment the resource is managed at all, and the Files
  # account's key is actively used (see app.tf) - fed straight into
  # azurerm_container_app_environment_storage, which has no Key-Vault reference option
  # the way the audit key does. Local, unencrypted state was already exposing that key;
  # a remote backend stops being a someday cleanup the moment that is true.
  backend "azurerm" {}
}

provider "azurerm" {
  subscription_id = var.subscription_id

  # Every provider this configuration needs is already registered on the sandbox, and
  # registering providers needs a subscription-level permission that a Contributor does
  # not have. Without this, azurerm 4.x fails at plan time trying to check them.
  resource_provider_registrations = "none"

  features {
    key_vault {
      # A sandbox has to be cleanable. Purge protection cannot be turned off once on,
      # so it stays off here and the vault is genuinely deletable.
      purge_soft_deleted_secrets_on_destroy = true
      recover_soft_deleted_key_vaults       = true
    }
  }
}

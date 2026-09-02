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

  # State is local, which is fine for a sandbox and wrong for anything shared: it holds
  # resource ids, the ACR login server, and whatever a future resource decides to
  # record. Moving it to a storage account backend is a bootstrap of its own (the
  # backend cannot be created by the configuration that uses it), so it is deliberately
  # not attempted here. If a second person ever runs this, do that first.
  #
  # The audit key is NOT in state: the Key Vault secret is created out of band and
  # referenced by a versionless URI. See deploy/README.md.
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

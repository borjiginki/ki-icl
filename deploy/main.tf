// The resource group and the names everything else hangs off.

resource "random_string" "suffix" {
  length  = 6
  lower   = true
  upper   = false
  numeric = true
  special = false

  # ACR and Key Vault names are globally unique, so a prefix alone collides with
  # somebody else's playground. Kept out of lifecycle churn: regenerating it would
  # rename the registry and the vault, which means recreating both.
  keepers = {
    resource_group = var.resource_group_name
  }
}

locals {
  # ACR allows alphanumeric only, no hyphens, 5 to 50 characters.
  acr_name = "${var.name_prefix}acr${random_string.suffix.result}"
  # Key Vault allows 3 to 24 characters, alphanumeric and hyphens.
  vault_name = "${var.name_prefix}-kv-${random_string.suffix.result}"

  # Empty `image` means run Microsoft's quickstart instead, so the first apply proves
  # the infrastructure without the application being involved. See variables.tf.
  image          = var.image != "" ? var.image : "mcr.microsoft.com/k8se/quickstart:latest"
  serving_ki_icl = var.image != ""
  identifier_uri = var.entra.identifier_uri != "" ? var.entra.identifier_uri : "api://${var.entra.client_id}"
}

resource "azurerm_resource_group" "this" {
  name     = var.resource_group_name
  location = var.location
  tags     = var.tags
}

# A `check` block only warns, so anything that would guarantee a broken or unlogged
# deployment is a `precondition` on the app instead, which fails the apply. The one
# genuine judgement call is left as a warning, because it is a judgement call.
#
# `demo` is refused by the auth_mode variable's own validation rather than here: it
# depends on one variable, so a variable validation catches it earlier and harder.

check "grants_are_enforced_once_there_is_somebody_to_enforce_against" {
  assert {
    condition     = !(var.auth_mode == "entra" && !var.enforce_grants)
    error_message = "auth_mode = \"entra\" with enforce_grants = false serves every domain to every authenticated colleague and only logs what it would have withheld. That is a legitimate rollout step, and it is worth being told about, which is why this warns rather than fails."
  }
}

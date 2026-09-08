variable "subscription_id" {
  description = "The KI-PER Data Platform Sandbox (Sponsorship 2026). Named explicitly rather than taken from the CLI's default, because the CLI default is the AI PLATFORM subscription and a mistake there would be a production mistake."
  type        = string
  default     = "744a1376-67fc-40a3-acd7-1cf437f7c02a"
}

variable "resource_group_name" {
  description = "Created by this configuration, so it is destroyable as one unit."
  type        = string
  default     = "ki-icl-sandbox"
}

variable "location" {
  description = "Germany West Central: every existing group in the sandbox is there, and an EU region is a requirement rather than a preference. The access log carries a pseudonym derived from a person, so the Log Analytics workspace holding it must not leave the EU (Art. 44)."
  type        = string
  default     = "germanywestcentral"
}

variable "name_prefix" {
  description = "Prefix for every resource name. Short, because Key Vault allows 24 characters and a random suffix eats six of them."
  type        = string
  default     = "kiicl"

  validation {
    condition     = can(regex("^[a-z][a-z0-9]{2,9}$", var.name_prefix))
    error_message = "Lowercase letters and digits, 3 to 10 characters, starting with a letter. ACR names allow nothing else."
  }
}

# --- what gets served -------------------------------------------------------

variable "image" {
  description = <<-EOT
    Full image reference to run. Leave empty for the first apply.

    The registry this creates is empty until something is pushed to it, so an app
    configured to pull from it on the first apply cannot start. Empty means "run
    Microsoft's quickstart image instead", which makes the first apply prove the
    infrastructure (VNet, environment, internal ingress, private DNS, Log Analytics)
    without the application being involved at all. When that is green:

      az acr build --registry <acr_name> --image ki-icl:<sha> ..
      terraform apply -var image=<acr_login_server>/ki-icl:<sha>

    Pin a digest or a commit sha, never `latest`, for the same review-trail reason as
    always, even though the corpus itself no longer travels with this tag - only the
    server code does. "Which corpus was served on Tuesday" is now answered by a share
    snapshot taken at publish time instead; see deploy/README.md Stage 2.
  EOT
  type        = string
  default     = ""
}

variable "auth_mode" {
  description = <<-EOT
    KI_ICL_AUTH: `off`, `demo` or `entra`.

    `off` is the day-one choice and is only defensible because ingress is internal:
    every caller that can reach the port reads the whole corpus, and the VNet is the
    only thing stopping them. The server says exactly that in its startup banner.

    `demo` will refuse to start here, by design: fake identities are loopback-only and
    a container binds 0.0.0.0.

    `entra` needs an app registration, which this tenant does not let ordinary users
    create. It is the intended end state, not the starting one.
  EOT
  type        = string
  default     = "off"

  validation {
    condition     = contains(["off", "entra"], var.auth_mode)
    error_message = "One of off, entra. There is no unset: serving unauthenticated is allowed only by saying so. `demo` is deliberately not deployable, because demo identities are loopback-only and a container binds 0.0.0.0, so the server would refuse to start."
  }
}

variable "enforce_grants" {
  description = "KI_ICL_ENFORCE. Irrelevant while auth_mode is `off`: with no identity there is nobody to enforce against, so the server observes and logs every would-be denial instead. It becomes load-bearing the moment entra lands."
  type        = bool
  default     = true
}

variable "entra" {
  description = "Entra resource-server settings. Required when auth_mode is `entra`, ignored otherwise. No client secret appears here, and none is needed: the server is a pure resource server holding only a public JWKS URL."
  type = object({
    tenant_id      = string
    client_id      = string
    identifier_uri = optional(string, "")
  })
  default = {
    tenant_id = ""
    client_id = ""
  }
}

variable "audit_key_enabled" {
  description = <<-EOT
    Whether to wire KI_ICL_AUDIT_KEY from Key Vault into the app.

    False on the first apply, because the vault exists before the secret does and a
    Container App whose secret reference cannot resolve fails to start. The server
    handles the absence honestly: it serves normally, records `audit: unkeyed` on the
    start record, and omits the actor field entirely rather than writing a placeholder.

    To turn it on: `az keyvault secret set` (see deploy/README.md), then flip this.
  EOT
  type        = bool
  default     = false
}

variable "audit_key_secret_name" {
  description = "Name of the Key Vault secret holding the HMAC key. Set out of band so the value never enters Terraform state."
  type        = string
  default     = "context-audit-key"
}


# --- knobs with defensible defaults ----------------------------------------

variable "create_role_assignments" {
  description = <<-EOT
    Whether to create the two role assignments the app needs (AcrPull on the registry,
    Key Vault Secrets User on the vault).

    Writing role assignments needs Microsoft.Authorization/roleAssignments/write, which
    Owner and User Access Administrator have and Contributor does not. If apply fails on
    authorization, set this false and ask somebody who can to grant those two roles to
    the managed identity named in the outputs. Everything else applies either way.
  EOT
  type        = bool
  default     = true
}

variable "acr_pull_confirmed" {
  description = <<-EOT
    Whether AcrPull has been confirmed granted to the managed identity some other way,
    when create_role_assignments = false so this configuration isn't the one that
    granted it.

    Exists because the precondition guarding a real `image` value has no way to check
    real Azure state on its own - it can only see this configuration's own variables.
    Without this, there was no way to satisfy that precondition except by setting
    create_role_assignments = true, even after confirming out of band (e.g.
    `az role assignment list --assignee-object-id <managed_identity_principal_id>`)
    that the grant already exists. Set this true only after checking yourself, not on
    someone else's say-so: a role assignment can exist and still target the wrong
    principal, as happened here once already (an app registration's client id was used
    where the principal/object id was needed).
  EOT
  type        = bool
  default     = false
}

variable "log_retention_days" {
  description = "90 days. One quarter covers a realistic incident-investigation window, and longer retention of person-linked read history is not necessary for the stated purposes and therefore not lawful to keep under Art. 5(1)(c) and (e). This is where that decision is actually enforced, because the container writes only to stderr."
  type        = number
  default     = 90
}

variable "vnet_address_space" {
  description = "Private range for the environment's VNet."
  type        = string
  default     = "10.60.0.0/16"
}

variable "infrastructure_subnet_prefix" {
  description = "Delegated to Microsoft.App/environments. A /23 rather than the /27 workload profiles technically allow, because address space is free and a subnet that turns out to be too small cannot be resized without recreating the environment."
  type        = string
  default     = "10.60.0.0/23"
}

variable "min_replicas" {
  description = "1 rather than 0. Scale-to-zero would be cheaper, but a cold start on the first tool call of a conversation is latency an agent pays in the middle of answering, and the whole corpus is a few tens of kilobytes."
  type        = number
  default     = 1
}

variable "max_replicas" {
  description = "Reads are stateless and the corpus is tiny, so this is a ceiling against a runaway rather than a capacity plan."
  type        = number
  default     = 3
}

variable "external_ingress_enabled" {
  description = "Whether the app's ingress is public. False (default) keeps the deployment's one existing security boundary intact. True opens it to the internet, gated only by allowed_client_cidrs below - a deliberate, disclosed trade for reaching this from outside kiicl-vnet without a VPN gateway, meant for a testing window rather than a permanent posture."
  type        = bool
  default     = false
}

variable "allowed_client_cidrs" {
  description = "CIDR ranges allowed to reach the app when external_ingress_enabled = true. Ignored while ingress stays internal. Container Apps' ip_security_restriction is deny-by-default the moment any Allow rule exists, so listing nothing here with external_ingress_enabled = true would deny everyone, not open everyone."
  type = list(object({
    name        = string
    cidr        = string
    description = optional(string, "")
  }))
  default = []
}

variable "tags" {
  description = "Applied to every resource."
  type        = map(string)
  default = {
    project     = "ki-icl"
    purpose     = "context-layer-mcp"
    owner       = "h.borjigin"
    environment = "sandbox"
  }
}

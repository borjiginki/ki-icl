# Deploying the context server to Azure Container Apps

Target: the **KI-PER Data Platform Sandbox (Sponsorship 2026)** subscription,
`744a1376-67fc-40a3-acd7-1cf437f7c02a`, resource group `hurile-playground`, region
Germany West Central.

The region is not a preference.
The access log carries a keyed pseudonym derived from a person, which is personal data, so the Log Analytics workspace holding it stays in the EU.
Every other resource group in the sandbox is in `germanywestcentral` anyway.

## What this is

Seventeen resources: a VNet with a `/23` subnet delegated to Container Apps, an **internal** Container Apps environment, a private DNS zone so the internal FQDN resolves, a Log Analytics workspace, a container registry, a Key Vault, one user-assigned managed identity, and the app.

**Internal ingress is the security boundary of this deployment.**
The environment gets a private IP, not a public one, so nothing outside a linked VNet can reach it.
That is what makes running with `auth_mode = "off"` defensible on day one: the network is the control, and the server says exactly that in its startup banner rather than pretending otherwise.

The consequence is worth stating plainly: **claude.ai and Claude Desktop cannot reach this.**
Only Claude Code from inside a VNet linked to the private DNS zone, which in practice means the corporate network or a VPN.
Reaching it from claude.ai needs `external_enabled = true`, and that should not happen before Entra works.

## Prerequisites

- Terraform 1.6 or newer, and `az` logged in (`az login`).
- Docker is **not** needed. `az acr build` builds in the registry.
- Permission to write role assignments, which Owner and User Access Administrator have and Contributor does not. If apply fails on authorization, see [when role assignments fail](#when-role-assignments-fail).

## Stage 1: the infrastructure, with nothing of ours running

```bash
cd deploy
terraform init
terraform apply
```

`image` defaults to empty, which runs Microsoft's quickstart container instead of ours.
That is deliberate: the registry is empty until something is pushed to it, so an app configured to pull from it on the first apply cannot start.
Running the placeholder makes this stage prove the infrastructure (subnet delegation, internal environment, private DNS, log collection) with the application not involved at all, so a failure here is unambiguously infrastructure.

Check `terraform output what_is_running`. It will say so.

## Stage 2: build and run the real image

```bash
ACR=$(terraform output -raw acr_name)
SERVER=$(terraform output -raw acr_login_server)
TAG=$(git -C .. rev-parse --short HEAD)

az acr build --registry "$ACR" --image "ki-icl:$TAG" ..
terraform apply -var "image=$SERVER/ki-icl:$TAG"
```

Pin the commit sha, never `latest`.
The corpus is baked into the image precisely so that "which corpus was served on Tuesday" is answerable from the tag, and a moving tag throws that away.

The build validates the corpus before packaging it, so a missing `sensitivity` label fails the build rather than becoming a runtime denial.

At this point the server is running with `KI_ICL_AUTH=off`: every caller that can reach the port reads everything, grants are observed rather than enforced, and every would-be denial is logged with `effect: observed`.
That observation is the useful part of this stage. Query it before enforcing anything:

```kusto
ContainerAppConsoleLogs_CL
| where Log_s has "access_denied"
| project TimeGenerated, Log_s
| order by TimeGenerated desc
```

## Stage 3: the audit key

The vault is created by stage 1, but the secret is set by hand so the key never enters Terraform state.

```bash
VAULT=$(terraform output -raw key_vault_name)
az keyvault secret set --vault-name "$VAULT" --name context-audit-key \
  --value "$(openssl rand -hex 32)"

terraform apply -var "image=$SERVER/ki-icl:$TAG" -var audit_key_enabled=true
```

Keep a copy of that value somewhere durable before you forget it.
Rotating it is allowed and is a real operation: every pseudonym changes, `actor_key` changes with it so the break is visible in the log rather than looking like a hundred new people arriving on a Tuesday, and older records stay readable but stop being linkable to newer ones.

Until this stage the app runs unkeyed.
That is a designed degradation, not a bug: the server serves normally, records `audit: unkeyed` on its start record, and omits the actor field entirely rather than writing a guessable placeholder.

## Stage 4: real identities

This is the stage that needs somebody else, because **this tenant does not let ordinary users register applications** (`allowedToCreateApps: False`).
Ask whoever administers the tenant for an app registration with the following, and note that **no client secret is needed or wanted**: the server is a pure OAuth resource server holding only a public JWKS URL.

1. A single-tenant app registration in tenant `cbd1a264-94b1-4d60-b0f6-ca149e7aef80`.
2. **`requestedAccessTokenVersion: 2`** in the app manifest. A fresh registration defaults to v1, whose issuer is `https://sts.windows.net/<tid>/` rather than `https://login.microsoftonline.com/<tid>/v2.0`, and every single request would fail on an issuer mismatch. This is the one setting most likely to cost a day.
3. Expose an API with one scope named **`context.read`**, so the App ID URI becomes `api://<client-id>` and the full scope is `api://<client-id>/context.read`.
4. App **roles** matching [access-policy.yaml](../access-policy.yaml): `ctx.colleague`, `ctx.delivery`, `ctx.sales`, `ctx.finance`, `ctx.people`. Assignable to users and groups.
5. Assign those roles to people in Enterprise Applications. That assignment list is the ISO 27001 A.5.18 access-rights review artefact, which is the reason the policy is keyed on app roles rather than group ids.

Then:

```bash
terraform apply \
  -var "image=$SERVER/ki-icl:$TAG" \
  -var audit_key_enabled=true \
  -var auth_mode=entra \
  -var 'entra={tenant_id="cbd1a264-94b1-4d60-b0f6-ca149e7aef80", client_id="<client-id>"}'
```

Verify what a real token actually carries before trusting it, without printing the token:

```bash
az account get-access-token --resource "api://<client-id>" \
  | python3 ../scripts/inspect_claims.py
```

That reports `roles` as absent until the app-role assignment lands, which is exactly the state to check for.
An identity with no roles is authenticated and granted nothing, so under enforcement it reads nothing.
Adding the Azure CLI's client id (`04b07795-8ddb-461a-bbee-02f9e1bf7b46`) to the API's "Authorized client applications" is what makes that `az` command work at all; without it you get an opaque `AADSTS65001`.

## Reaching it

```bash
terraform output mcp_url
```

Resolvable only from a VNet linked to the private DNS zone.
`terraform output private_dns_zone` names it; linking another VNet to that zone is a separate, deliberate act, because it is the moment somebody decides who can read the corpus.

From a machine inside such a VNet:

```bash
claude mcp add --transport http ki-icl-azure "$(terraform output -raw mcp_url)"
```

Once `auth_mode = entra`, Claude Code will run the OAuth flow against Entra.
Whether it can do that against a tenant with no dynamic client registration needs checking against the real client, and `--client-id` exists for exactly that case.

## When role assignments fail

Writing role assignments needs `Microsoft.Authorization/roleAssignments/write`.
If apply fails on authorization:

```bash
terraform apply -var create_role_assignments=false
```

Then ask somebody with Owner or User Access Administrator to grant these to the principal in `terraform output managed_identity_principal_id`:

- **AcrPull** on the registry, or the app cannot pull its image.
- **Key Vault Secrets User** on the vault, or the audit key cannot be read.
- **Key Vault Secrets Officer** on the vault for *your own* account, or you cannot set the secret. An RBAC vault refuses its own creator by default, which is the most common way this pattern wastes an hour.

The preconditions on the app refuse to deploy a configuration that would need a role it does not have, rather than letting you discover it as a revision that will not start.

## Cost

`min_replicas = 1` rather than scale-to-zero, so this is not free.
One Consumption replica at 0.25 vCPU and 0.5 GiB, a Basic registry, a Standard vault and a small Log Analytics workspace is single-digit euros a month at this size.
Scale-to-zero would be cheaper, and it was rejected because a cold start lands in the middle of an agent answering a question.
Set `min_replicas = 0` if the sandbox budget matters more than that.

## Teardown

```bash
terraform destroy
```

The vault has purge protection **off** and seven-day soft delete, deliberately, so a playground is genuinely cleanable.
Purge protection cannot be turned off once enabled, and a sandbox that enables it leaves an undeletable vault behind.

That means the vault is genuinely deletable, including the audit key. Anything that needs the old key to interpret old log records needs the key kept somewhere else first.

## What this deployment does not decide

Nothing here is a substitute for the compliance work in the [Access control](../README.md#access-control) section of the main README.
In particular, a per-person read log over `hr` and `finance` content requires works council consultation under §87(1) no. 6 BetrVG before it holds real data, and this configuration makes it technically possible to start collecting that data.
`auth_mode = "off"` records no actor at all, so stages 1 to 3 do not begin that processing.
Stage 4 does.

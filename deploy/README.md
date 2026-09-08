# Deploying the context server to Azure Container Apps

Target: the **KI-PER Data Platform Sandbox (Sponsorship 2026)** subscription,
`744a1376-67fc-40a3-acd7-1cf437f7c02a`, resource group `ki-icl-sandbox`, region
Germany West Central.

The region is not a preference.
The access log carries a keyed pseudonym derived from a person, which is personal data, so the Log Analytics workspace holding it stays in the EU.
Every other resource group in the sandbox is in `germanywestcentral` anyway.

## What this is

Twenty-five resources at the first apply.
A VNet with a `/23` subnet delegated to Container Apps, and a second, undelegated `/27` subnet for private endpoints.
A Container Apps environment with a public load balancer, VNet-integrated so it can still reach the Files private endpoint.
One private DNS zone with its VNet link, for the Files private endpoint.
A Log Analytics workspace, a container registry, a Key Vault, one user-assigned managed identity, and the app.
One storage account, reachable only through its own private endpoint: an Azure Files share holding the domains corpus, mounted into the app from the first apply onward.

**The app's own ingress is the security boundary of this deployment, not the environment.**
The environment used to have a private IP instead of a public one, which meant nothing outside a linked VNet could reach it no matter what the app's own ingress said.
That turned out to matter: `var.external_ingress_enabled` alone could not reach the app from outside `kiicl-vnet`, because the environment itself stood in the way regardless of the app's own setting.
The environment now has a public load balancer instead, so `var.external_ingress_enabled` and the allow-list in `var.allowed_client_cidrs` (see [Reaching it](#reaching-it)) are what actually decide who can reach the app.
The default, `external_ingress_enabled = false`, keeps the app's own ingress internal-only (reachable only dapr-to-dapr, within the environment), so nothing is reachable by default even though the environment's load balancer is public.
That is what makes running with `auth_mode = "off"` defensible day to day: with the default, the network is the control, and the server says exactly that in its startup banner rather than pretending otherwise.
Setting `external_ingress_enabled = true` reaches the public internet, gated only by the IP allow-list - a deliberate, disclosed trade for a testing window, not a permanent posture, since `auth_mode` stays `off` until Entra lands.
The Files storage account follows a different, still-internal rule: `public_network_access_enabled = false`, with a private endpoint the only way in.

The Files storage account stays reachable only from inside `kiicl-vnet`, regardless of the app's own ingress: no VPN gateway, ExpressRoute, or VNet peering exists anywhere in this configuration.
So publishing the corpus (Stage 2) still needs the workaround that section describes, even once the app itself is reachable from outside the VNet.

## Prerequisites

- Terraform 1.6 or newer, and `az` logged in (`az login`).
- Docker is **not** needed. `az acr build` builds in the registry.
- Permission to write role assignments, which Owner and User Access Administrator have and Contributor does not. If apply fails on authorization, see [when role assignments fail](#when-role-assignments-fail).
- To run Stage 2 (publishing the corpus), permission to toggle the Files storage account's public network access (`Storage Account Contributor` or better) — see Stage 2 for why this is needed at all.

## Remote state

State lives in an Azure Storage blob, not on a laptop: local, unencrypted state was
already exposing the Files share's access key (see `versions.tf`), and CI needs a state
it can read regardless of whose machine ran the last apply.

The backend storage account cannot be created by the configuration that uses it, so it
is bootstrapped once, by hand, in its own resource group, outside `ki-icl-sandbox` so
that a `terraform destroy` of the sandbox can never delete the state describing that
destroy while it is still running:

```bash
az group create --name ki-icl-tfstate --location germanywestcentral

az storage account create --name <globally-unique-name> --resource-group ki-icl-tfstate \
  --location germanywestcentral --sku Standard_LRS --kind StorageV2 \
  --min-tls-version TLS1_2 --allow-blob-public-access false

az storage container create --name tfstate --account-name <name> --auth-mode login
```

Reachable over the public internet on purpose (see [What this is](#what-this-is) for the
same trade made on the app's own ingress): a private endpoint would need a network path
in from GitHub-hosted runners that does not exist, and this account's access key is the
real gate regardless, the same "key, not network, is the boundary" pattern this
deployment already uses for the Files share. Write the account and container names into
`backend.hcl` (not secret, already committed); the access key itself is never written to
a file, here or anywhere else - it is read into `ARM_ACCESS_KEY` at init time, locally
and in CI alike.

## Stage 1: the infrastructure, with nothing of ours running

```bash
cd deploy
export ARM_ACCESS_KEY=$(az storage account keys list --account-name <tfstate-account> \
  --resource-group ki-icl-tfstate --query '[0].value' -o tsv)
terraform init -backend-config=backend.hcl
terraform apply
```

`image` defaults to empty, which runs Microsoft's quickstart container instead of ours.
That is deliberate: the registry is empty until something is pushed to it, so an app configured to pull from it on the first apply cannot start.
Running the placeholder makes this stage prove the infrastructure (subnet delegation, the environment, private DNS, log collection, the storage account, the private endpoint) with the application not involved at all, so a failure here is unambiguously infrastructure.

This also creates the domains corpus's file share, empty until Stage 2 publishes to it.

Check `terraform output what_is_running`. It will say so.

## Stage 2: the corpus, on the file share

The share needs to hold a complete, valid tree before Stage 3 ever points this deployment at the real image, because **the server refuses to start if `access-policy.yaml` can't be read from `CONTEXT_ROOT`** (`server/mcp_server.py` exits at boot when the policy has no roles).
Publish before building the real image, or a revision crash-loops on a share that is either empty or missing the policy file.

**Reaching the share at all needs a workaround right now, and it's worth understanding before running the commands.** The Files account has no public endpoint - only a private one, inside `kiicl-vnet`. Nothing links that VNet to anywhere else yet (see [What this is](#what-this-is)), so no machine outside it, including yours, can reach the share as things stand. `make publish` (below) handles this by opening a narrow, temporary window: it re-enables public access, does the upload, lists the result, snapshots, then disables public access again in a trap - even if a step fails partway. It is a disclosed, deliberate trade against building real VPN or peering connectivity just to run this command occasionally; revisit it if publishing becomes frequent enough for the open window to matter.

```bash
ACCOUNT=$(terraform output -raw files_storage_account_name)

make publish ACCOUNT="$ACCOUNT"
```

The mount itself needs no separate apply: the Container App's environment storage link takes its access key directly from the storage account resource (see the comment on `azurerm_container_app_environment_storage.context` in `app.tf`), not from a variable, so publishing content is the only step here.

`make publish` validates the corpus, packages it — exactly what CI already does on every push — uploads `dist/staging/` (byte-identical to what the mount will serve) to the share, lists what landed so the upload is visible without a second command, and takes a share snapshot.
That snapshot is this deployment's replacement for the property the image tag used to carry: it cannot answer "which corpus was served on Tuesday" as precisely as a pinned image tag did, but it gives the share's content a point-in-time record, which a plain mount would not otherwise have.

`make publish` resolves the storage account key it uploads with itself, through your own `az login` session, rather than needing the key you just set in the vault — see the comment above the target in the `Makefile` if that fails on authorization.

**Removing an artifact is safe to publish routinely, with no unsafe window.**
`az storage file upload-batch` only adds and overwrites, it never deletes, but that turns out not to matter for correctness: `make publish` regenerates every domain's manifest fresh on every run, so a removed artifact's id simply stops resolving on the read path the moment the new manifest lands — even before its now-orphaned files are physically deleted from the share.

Actually reclaiming that storage, or fully scrubbing the bytes of something removed, is a separate, deliberately rarer act, wrapped in the same access-window pattern:

```bash
make purge-and-republish ACCOUNT="$ACCOUNT"
```

This wipes the share and republishes within the same open window, so there is no gap where the share is both empty and reachable at once - there is still a brief window where it is empty, which is why this is its own command rather than something `publish` does by default. Only run it when you mean to.

## Stage 3: build and run the real image

```bash
ACR=$(terraform output -raw acr_name)
SERVER=$(terraform output -raw acr_login_server)
TAG=$(git -C .. rev-parse --short HEAD)

az acr build --registry "$ACR" --image "ki-icl:$TAG" ..
terraform apply -var "image=$SERVER/ki-icl:$TAG"
```

Pin the commit sha, never `latest`, for the same review-trail reason as always, even though the corpus itself no longer travels with this tag — only the server code does now.

The build no longer validates or packages the corpus (that gate ran in Stage 2, and already runs in CI on every push regardless); it only builds the server image, which is smaller and needs no `.git` history at all now that packaging has left Docker entirely.

At this point the server is running with `KI_ICL_AUTH=off`: every caller that can reach the port reads everything, grants are observed rather than enforced, and every would-be denial is logged with `effect: observed`.
That observation is the useful part of this stage. Query it before enforcing anything:

```kusto
ContainerAppConsoleLogs_CL
| where Log_s has "access_denied"
| project TimeGenerated, Log_s
| order by TimeGenerated desc
```

## Stage 4: the audit key

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

## Stage 5: real identities

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

Publicly resolvable now that the environment's load balancer is public (see [What this is](#what-this-is)), but reachable only when `external_ingress_enabled = true`, and only from an IP listed in `allowed_client_cidrs`:

```bash
terraform apply \
  -var external_ingress_enabled=true \
  -var 'allowed_client_cidrs=[{name="<you>", cidr="<your-public-ip>/32", description="<why>"}]'
```

Find your own public IP with `curl https://api.ipify.org`, and update the allow-list whenever it changes.
This is a deliberate, disclosed trade: with `auth_mode` still `off`, the IP allow-list is the only gate, meant for a testing window rather than a permanent posture.
Revisit it once `auth_mode = entra` is real, at which point the token requirement carries that weight instead.

From your own machine, once reachable:

```bash
claude mcp add --transport http ki-icl-azure "$(terraform output -raw mcp_url)"
```

Once `auth_mode = entra`, Claude Code will run the OAuth flow against Entra.
Whether it can do that against a tenant with no dynamic client registration needs checking against the real client, and `--client-id` exists for exactly that case.

## CI/CD

`.github/workflows/deploy.yml` runs on every push to `main`: builds and pushes the
image, publishes the corpus if `domains/` or `access-policy.yaml` changed, then plans
the infrastructure. It authenticates as a dedicated service principal (Contributor on
the subscription, four secrets: `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`,
`AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`), separate from the identity anyone applies
locally with, and reads/writes the same remote state described above (a fifth secret,
`TF_STATE_ACCESS_KEY`).

**Applying is the one step that waits for a human.** The `terraform-apply` job targets
the `azure-infra` GitHub Environment, which requires approval before it runs - the plan
is visible first, in the `terraform-plan` job's log, so approving is not blind. This
stays manual on purpose: an incremental-looking Terraform change forced a full
environment replacement earlier in this deployment's life (see [What this
is](#what-this-is)), discovered only by testing reachability for real rather than
trusting the plan's own summary line, and that is exactly the kind of surprise this
gate exists to catch before it reaches real infrastructure unattended.

**External ingress is deliberately not something CI manages.** `deploy/ci.auto.tfvars`
carries the baseline every apply needs (`create_role_assignments = false`,
`acr_pull_confirmed = true`), but `external_ingress_enabled` and `allowed_client_cidrs`
stay a manual override you apply yourself when you want to reach the app from outside
`kiicl-vnet` (see [Reaching it](#reaching-it)). A CI-triggered apply that ran without
them would reset ingress to internal-only, the variable's default - if you still want
external access afterward, re-run your own apply with those two flags.

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

Separately, `make publish` (Stage 2) needs *your own* identity to be able to read the Files storage account's key, which role assignments above do not grant — that needs a data-plane-adjacent role such as **Storage Account Contributor** (or Owner) on the files storage account, not on the app's managed identity.

## Cost

`min_replicas = 1` rather than scale-to-zero, so this is not free.
One Consumption replica at 0.25 vCPU and 0.5 GiB, a Basic registry, a Standard vault, a small Log Analytics workspace, a private endpoint, and one Standard/LRS storage account holding a corpus in the hundreds of kilobytes, is still single-digit euros a month at this size.
Scale-to-zero would be cheaper, and it was rejected because a cold start lands in the middle of an agent answering a question.
Set `min_replicas = 0` if the sandbox budget matters more than that.

## Teardown

```bash
terraform destroy
```

The vault has purge protection **off** and seven-day soft delete, deliberately, so a playground is genuinely cleanable.
Purge protection cannot be turned off once enabled, and a sandbox that enables it leaves an undeletable vault behind.

That means the vault is genuinely deletable, including the audit key. Anything that needs the old key to interpret old log records needs the key kept somewhere else first.

The storage account carries no equivalent protection and destroys cleanly with everything else. If the Files share's key was ever copied anywhere outside the vault (a terminal history, a note), destroying the account does not revoke a copy that already left Azure — treat it as compromised the same way you would any other retired credential.

## What this deployment does not decide

Nothing here is a substitute for the compliance work in the [Access control](../README.md#access-control) section of the main README.
In particular, a per-person read log over `hr` and `finance` content requires works council consultation under §87(1) no. 6 BetrVG before it holds real data, and this configuration makes it technically possible to start collecting that data.
`auth_mode = "off"` records no actor at all, so stages 1 to 4 do not begin that processing.
Stage 5 does.

# Serving the usage dashboard from the MCP server, behind an off-by-default switch

**Date:** 2026-09-10
**Status:** approved, not yet implemented
**Scope:** `server/`, `scripts/dashboard.py`, `deploy/`, `.github/workflows/deploy.yml`, tests, README

## The problem

The usage dashboard is not reachable from anywhere outside the machine it runs on.
[scripts/dashboard.py](../../../scripts/dashboard.py) binds `127.0.0.1:8010` and reads `logs/usage.jsonl`, and the deployed Azure app has neither of those things.
The [Dockerfile](../../../Dockerfile) copies only `server/`, so the script is not in the image at all, and both the image and [deploy/app.tf](../../../deploy/app.tf) set `CONTEXT_USAGE_LOG=""`, which disables the file sink and leaves stderr as the only one.

So the dashboard needs a route to it, and it needs records to draw.
Exposing the existing script on the existing allow-list would render an empty page, because there is no `usage.jsonl` anywhere in Azure.

## What this is for

A demo, reached by one person, from one IP already on `allowed_client_cidrs`.
That is the whole requirement, and it is what makes the design below proportionate.
It is explicitly not an operator console for a production deployment: the dashboard is POC scaffolding that its own module docstring says should be deleted rather than ported to `ki-mcp`, because in production Log Analytics does this job.

## Non-goals

- No authentication of its own. The IP allow-list is the gate, and section "What this exposes" says what that costs.
- No durable store for the records. History resetting on redeploy is accepted, not worked around.
- No second Container App, no second Files share, no change to where the audit log lives.
- No change to the default posture. With the switch unset, the deployment is bit-for-bit what ships today.

## Decision

**Serve the dashboard from the MCP server process itself, on the port and ingress that already exist, behind an environment switch that is off by default.**

Two alternatives were considered and rejected.

A second Container App, `kiicl-dashboard`, with its own ingress and allow-list, would keep operator routes out of the server.
But two processes then need a shared log, which means a read-write Azure Files share written by the server and read by the dashboard.
That is a second audit store next to Log Analytics, and the 90-day retention stops being one setting with one owner, which is the property [Dockerfile](../../../Dockerfile) is careful about where it sets `CONTEXT_USAGE_LOG=""`.
Roughly four times the infrastructure and a real compliance cost, to host a tool that is meant to be deleted.

Keeping the dashboard on loopback and feeding it real records exported from Log Analytics adds no public surface at all.
But Log Analytics ingestion runs minutes behind, so the live "watch the miss appear" moment that makes the dashboard worth demoing does not work, and it does not deliver what was asked for.

## Design

### 1. Module split

[scripts/dashboard.py](../../../scripts/dashboard.py) is 645 lines: pure aggregation, curation, purge, then a `BaseHTTPRequestHandler` and a `main`.
The aggregation is the part the server needs to import, and the image copies only `server/`.

**New `server/dashboard.py`** takes, unchanged: `read_records`, `read_curation`, `_write_curation`, `curate`, `purge`, `_is_demand_for`, `filter_records`, `_utcnow`, `_key`, `_pct`, `aggregate`, `_kpi`, `_misses`, `_served`, `_funnel`, `_offered`, `_activity`, `_versions`, `_latency`, `live_catalog`, and the `CURATION_STATES` / `_RETURNS_ON_DEMAND` constants.
It gains one new function, `register(mcp)`, which mounts the routes described below.

**`scripts/dashboard.py`** keeps only `Handler` and `main`, importing the rest from `server.dashboard`.
`make dashboard` on `:8010` behaves exactly as it does now, and that is the point: the loopback path is the reference behaviour the deployed path has to match.

Copying `scripts/` into the image instead would drag `demo.py`, `usage_report.py` and `inspect_claims.py` into a production image for no reason, so the module split is both cheaper and more honest.

The path constants move with their functions.
`LOG` and `CURATION` are already environment-driven (`CONTEXT_USAGE_LOG`, `CONTEXT_CURATION`), and their `ROOT`-relative defaults resolve to the same place from `server/` as from `scripts/`, since both are one level below the repository root.

### 2. The switch and the routes

`KI_ICL_DASHBOARD` is read once at module level, the pattern `USAGE_LOG_PATH` and `_AUDIT_KEY` already use.
Unset means `register()` is never called and not one route exists.

When it is set, four routes are registered through `mcp.custom_route(..., include_in_schema=False)`:

| Route | Method | Serves |
|---|---|---|
| `/dashboard` | GET | [server/dashboard.html](../../../server/dashboard.html), already in the image |
| `/dashboard/data` | GET | the aggregate JSON, honouring the `domain` and `hours` query parameters |
| `/dashboard/curate` | POST | mark one suggestion |
| `/dashboard/purge` | POST | delete one suggestion, rewriting the log |

Verified against the installed FastMCP 3.4.7: custom routes land at the root of the same Starlette app that serves `/mcp`, so the deployed URL is `https://<fqdn>/dashboard` with no prefix and no second port.

The handlers are thin adapters over the functions in section 1.
The request-body validation, the 400 and 404 behaviour, and the rule that `/curate`'s demand baseline is computed server-side rather than taken from the client, all carry over from `Handler.do_POST` unchanged.

### 3. The page's base path

[server/dashboard.html](../../../server/dashboard.html) hardcodes `/data`, `/curate` and `/purge` at two call sites.
One constant fixes both for both hosts:

```js
const BASE = location.pathname.replace(/\/+$/, "");
```

Served at `/` by the loopback script, `BASE` is `""` and the existing URLs are unchanged.
Served at `/dashboard`, everything hangs off that.
No server-side HTML rewriting, and one page that works under either host.

### 4. Where the records come from

`CONTEXT_USAGE_LOG=/app/logs/usage.jsonl`, set in [deploy/app.tf](../../../deploy/app.tf) only when the switch is on.
That re-enables the file sink in `UsageLog.write`, which already writes to both sinks and already does `mkdir(parents=True, exist_ok=True)`.

Log Analytics keeps receiving every line exactly as it does now.
The audit store, its retention and its owner are untouched, and the file is an ephemeral second copy whose only reader is the dashboard.
`/app` is `chown app:app` in the image and the read-only Files mount is at `/app/context`, so `/app/logs` is writable by uid 10001.
`CONTEXT_CURATION` lands beside it at `/app/logs/curation.json`.

The file dies with the revision, so a redeploy resets the dashboard's history.
That is the honest cost of not adding a store, and for a demo it reads as a feature rather than a defect.

### 5. Terraform

One new variable, `dashboard_enabled`, `bool`, default `false`.
It is declared from repository settings through a `DASHBOARD_ENABLED` repository variable read in [.github/workflows/deploy.yml](../../../.github/workflows/deploy.yml), alongside `EXTERNAL_INGRESS_ENABLED` and `PUBLISH_RUNNER_ENABLED`, for the reason the comment above those already gives: a switch set by a local apply is silently revoked by the next merge to main, and the first symptom is the thing not being reachable.

In [deploy/app.tf](../../../deploy/app.tf), `KI_ICL_DASHBOARD` becomes a `dynamic "env"` on the flag, and the currently-static `CONTEXT_USAGE_LOG = ""` becomes conditional on it.

A `dashboard_url` output, so the URL does not have to be assembled by hand.

### 6. Preconditions

Three, all hard failures on the `azurerm_container_app` resource, in the style of the four already there.

1. **`dashboard_enabled` requires `external_ingress_enabled`.**
   Otherwise the flag claims something nobody can reach, and the failure is silent.
2. **`dashboard_enabled` requires `max_replicas == 1`.**
   Each replica writes its own file, so with the default ceiling of 3 the page shows whichever replica the load balancer happened to pick, and the numbers are wrong in a way nothing on the page reveals.
   A precondition rather than a silent override, so the trade stays visible in the configuration.
3. **`dashboard_enabled` and `auth_mode == "entra"` cannot both be true.**
   The dashboard has no authentication of its own and its corpus index bypasses both grant seams, so once real identities exist an IP allow-list is no longer a defensible gate.
   This is the guard that stops demo scaffolding surviving into production by inertia.

## What this exposes

With the switch on, and only then:

- `/dashboard/data` includes `live_catalog()`, which reads `_manifest.json` directly and lists **every artifact id in the corpus regardless of who may read it**.
  Both `live_catalog` and `usage.catalog_record` document this deliberate bypass, and both say the thing rendering it must be access-controlled at deployment.
  Here the access control is the IP allow-list, and nothing else.
- `/dashboard/purge` rewrites the usage log in place.
  It is the one route in the system that edits the audit trail, and it has no authentication of its own.

What it does **not** expose is personal data.
With `auth_mode = "off"` and no audit key set, `identity.audit_fields` records no `actor` at all, so the log holds no identifier for any person.
The §87(1) no. 6 BetrVG works council item that blocks `auth_mode = "entra"` therefore does not attach to this change; precondition 3 is what keeps that true, by making the two mutually exclusive in configuration rather than by convention.

This is a disclosed trade for a demo reached by one person, on the same footing as `external_ingress_enabled` itself, and it inherits that variable's "testing window, not a permanent posture" framing.

## Testing

Test-driven, and the first test written is the guard, not a feature.

**The control that must not be lost.** `AGGREGATORS` in [tests/test_purpose_limitation.py](../../../tests/test_purpose_limitation.py) is a hardcoded tuple of file paths, and it is what makes the purpose limitation real: no tool in this repo may group, rank or count by who the caller was.
Moving the aggregation out from under that tuple would retire the control silently and still show green.
`server/dashboard.py` is added to `AGGREGATORS` in the same commit as the move, and `test_the_aggregators_do_not_import_identity` must hold for it too.

**The move is behaviour-preserving.** `tests/test_dashboard.py`, `tests/test_curation.py` and `tests/test_purge.py` are 878 lines between them.
Their import lines change to `server.dashboard` and nothing else changes.
Those suites passing unmodified is the evidence that section 1 moved code rather than rewrote it.

**New tests.**

- With `KI_ICL_DASHBOARD` unset, the app exposes no `/dashboard*` route at all. Asserted against the route table, not only against a 404, so a future catch-all cannot make this pass for the wrong reason.
- With it set, `GET /dashboard` returns the page and `GET /dashboard/data` returns an aggregate matching what `aggregate()` returns for the same records.
- `domain` and `hours` query parameters filter as they do on the loopback host.
- `POST /dashboard/curate` and `POST /dashboard/purge` have the same effects, and the same 400 and 404 behaviour, as `Handler.do_POST`.
- The page's `BASE` resolves to `""` at `/` and to `/dashboard` at `/dashboard`, so the same file works under both hosts.

## Documentation

- README's "The dashboard" section: the switch, the deployed URL, and what it exposes.
- `deploy/README.md`, "Reaching it": `DASHBOARD_ENABLED` beside `EXTERNAL_INGRESS_ENABLED`, and the three preconditions.
- `deploy/variables.tf`: the new variable carries its own reasoning, as every variable in that file does.

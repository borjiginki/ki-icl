.PHONY: help install validate package publish purge-and-republish test serve serve-http demo clean

PY := .venv/bin/python

help:
	@echo "Targets:"
	@echo "  install     create .venv and install dependencies"
	@echo "  validate    run the structural validation gate over domains/"
	@echo "  package     build dist/context/ (upload dir) and dist/staging/ (servable tree)"
	@echo "  publish     package, then upload dist/staging/ to the Files share (ACCOUNT=... [RESOURCE_GROUP=...])"
	@echo "  purge-and-republish  wipe the share and republish - rare, reclaims space (ACCOUNT=...)"
	@echo "  test        run the test suite"
	@echo "  serve       run the MCP server over stdio, serving dist/staging"
	@echo "  serve-http  same, over HTTP on 127.0.0.1:8000/mcp, unauthenticated"
	@echo "  serve-http-demo  over HTTP, authenticated with the demo identities"
	@echo "  serve-as    over stdio as a demo identity, with enforcement on"
	@echo "  demo        package, then walk the acceptance demo end to end"
	@echo "  inspector   serve over HTTP and open MCP Inspector against it"
	@echo "  usage       summarise logs/usage.jsonl: what was looked up, and what was missed"
	@echo "  dashboard   serve the usage dashboard on :8010, reading the log live"

install:
	python3 -m venv .venv
	$(PY) -m pip install -q --upgrade pip
	$(PY) -m pip install -q -r requirements-dev.txt

validate:
	$(PY) scripts/validate_context.py

package: validate
	$(PY) scripts/package_context.py

# Requires ACCOUNT: the Files storage account name (`terraform output -raw
# files_storage_account_name` from deploy/). Uploads what CONTEXT_ROOT will read from
# the mount, lists the result so the upload is visible without a second command, then
# takes a share snapshot as a point-in-time record, since the image tag no longer
# answers "what was served when" for a mounted corpus. See deploy/README.md.
#
# Safe to re-run for ordinary edits and additions, and even for an artifact removed from
# domains/: package regenerates every manifest fresh on every run, and upload-batch
# overwrites it, so a delisted id simply stops resolving on the read path immediately,
# before its now-orphaned files are ever physically deleted. upload-batch itself never
# deletes, so reclaiming that storage, or fully scrubbing removed bytes, needs
# `make purge-and-republish` instead - a separate, deliberately rarer target.
#
# No key or SAS token is passed: omitting both makes the CLI resolve the account key
# itself via the operator's own `az login` RBAC (Storage Account Key Operator Service
# Role or above), so the human running this never handles the raw key at all. That
# needs real RBAC on the account, not just data-plane rights - see deploy/README.md if
# this fails on authorization.
#
# Brackets everything in a temporary public-access window: the account normally has
# public_network_access_enabled = false (see deploy/storage.tf), and as of this
# writing nothing links kiicl-vnet to any other network, so nothing outside it -
# including wherever this target runs from - can reach the account's private endpoint
# at all. The trap always closes the window again, even if any step fails partway, so
# a failed publish does not silently leave the share publicly reachable. This is a
# deliberate, disclosed workaround for not having real VPN/peering connectivity into
# kiicl-vnet yet, not the long-term shape: worth revisiting if publishing becomes
# frequent enough for the open window to matter.
RESOURCE_GROUP ?= ki-icl-sandbox

publish: package
	@test -n "$(ACCOUNT)" || (echo "Usage: make publish ACCOUNT=<files storage account name>" >&2 && exit 1)
	az storage account update --name "$(ACCOUNT)" --resource-group "$(RESOURCE_GROUP)" \
	  --public-network-access Enabled --output none
	@echo "waiting for public access to propagate..." && sleep 20
	@trap 'az storage account update --name "$(ACCOUNT)" --resource-group "$(RESOURCE_GROUP)" \
	  --public-network-access Disabled --output none' EXIT; \
	  az storage file upload-batch --destination context --source dist/staging \
	    --account-name "$(ACCOUNT)" && \
	  az storage file list --share-name context --account-name "$(ACCOUNT)" -o table && \
	  az storage share snapshot --name context --account-name "$(ACCOUNT)"

# The separate, deliberately rarer path for actually reclaiming storage or scrubbing
# the bytes of a removed artifact - see the comment above `publish` for why the
# routine path never needs this. Wipes the share and republishes in the same access
# window, so there is no gap where the share is empty and reachable at once; there is
# still a brief window where it is empty, which is why this is its own command rather
# than something `publish` does by default.
purge-and-republish: package
	@test -n "$(ACCOUNT)" || (echo "Usage: make purge-and-republish ACCOUNT=<files storage account name>" >&2 && exit 1)
	az storage account update --name "$(ACCOUNT)" --resource-group "$(RESOURCE_GROUP)" \
	  --public-network-access Enabled --output none
	@echo "waiting for public access to propagate..." && sleep 20
	@trap 'az storage account update --name "$(ACCOUNT)" --resource-group "$(RESOURCE_GROUP)" \
	  --public-network-access Disabled --output none' EXIT; \
	  az storage file delete-batch --source context --account-name "$(ACCOUNT)" --pattern '*' && \
	  az storage file upload-batch --destination context --source dist/staging \
	    --account-name "$(ACCOUNT)" && \
	  az storage share snapshot --name context --account-name "$(ACCOUNT)"

test:
	$(PY) -m pytest -q

serve: package
	CONTEXT_ROOT=dist/staging $(PY) server/mcp_server.py

# KI_ICL_AUTH=off says out loud that this port is unauthenticated. The server refuses
# to start over HTTP without one of entra/demo/off, so a forgotten variable is a server
# that does not come up rather than one that serves everything to anyone.
serve-http: package
	CONTEXT_ROOT=dist/staging KI_ICL_AUTH=off $(PY) server/mcp_server.py --http

# Authenticated with the fake identities in config/demo_principals.yaml. Pass a token as
# `Authorization: Bearer demo-token-<id>`; see that file for what each one proves.
#
# The fallback audit key is committed on purpose and is NOT a secret: it exists so the
# pseudonyms in a local run are stable and comparable. It must never reach a deployment.
# Anyone with this repo can dictionary-attack pseudonyms minted with it, which is the
# whole reason server/identity.py insists the key be secret. Demo mode refuses to bind
# off loopback, so this target cannot serve anything but this machine.
serve-http-demo: package
	CONTEXT_ROOT=dist/staging KI_ICL_AUTH=demo KI_ICL_ENFORCE=1 \
	  KI_ICL_AUDIT_KEY=$${KI_ICL_AUDIT_KEY:-local-demo-key-not-a-secret-0123456789} \
	  $(PY) server/mcp_server.py --http

# Enforcement over stdio, by adopting a demo identity. The only way to see filtering
# locally without HTTP, since stdio carries no token and so observes by default.
# DEV_PRINCIPAL=baseline make serve-as
DEV_PRINCIPAL ?= baseline

serve-as: package
	CONTEXT_ROOT=dist/staging KI_ICL_DEV_PRINCIPAL=$(DEV_PRINCIPAL) KI_ICL_ENFORCE=1 \
	  $(PY) server/mcp_server.py

# Ports are overridable because the Inspector's defaults (6274/6277) collide with
# any other Inspector already running, and it fails rather than falling back.
INSPECTOR_CLIENT_PORT ?= 6374
INSPECTOR_SERVER_PORT ?= 6377

inspector: package
	@echo "Serving http://127.0.0.1:8000/mcp"
	@CONTEXT_ROOT=dist/staging $(PY) server/mcp_server.py --http & \
	  server_pid=$$!; \
	  trap 'kill $$server_pid 2>/dev/null' EXIT INT TERM; \
	  sleep 2; \
	  CLIENT_PORT=$(INSPECTOR_CLIENT_PORT) SERVER_PORT=$(INSPECTOR_SERVER_PORT) \
	    MCP_SANDBOX_PORT=$$(($(INSPECTOR_CLIENT_PORT)+1)) \
	    MCP_APP_ORIGIN_PORT=$$(($(INSPECTOR_SERVER_PORT)+1)) \
	    npx -y @modelcontextprotocol/inspector \
	      --config mcp-inspector.json --server ki-icl-http

usage:
	@$(PY) scripts/usage_report.py

DASHBOARD_PORT ?= 8010

dashboard:
	@CONTEXT_ROOT=dist/staging DASHBOARD_PORT=$(DASHBOARD_PORT) $(PY) scripts/dashboard.py

demo: package
	CONTEXT_ROOT=dist/staging $(PY) scripts/demo.py

clean:
	rm -rf dist

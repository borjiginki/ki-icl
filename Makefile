.PHONY: help install test serve serve-http serve-http-demo serve-as demo inspector usage dashboard

PY := .venv/bin/python

# The packaged corpus this server reads. domains/ and access-policy.yaml moved to
# ki-ccl (github.com/ki-group-gmbh/ki-ccl); its own `make package` produces
# dist/staging/, which this defaults to reading from a sibling checkout. Override for
# any other packaged tree, e.g. one downloaded from a share snapshot.
CONTEXT_ROOT ?= ../ki-ccl/dist/staging

help:
	@echo "Targets:"
	@echo "  install     create .venv and install dependencies"
	@echo "  test        run the test suite"
	@echo "  serve       run the MCP server over stdio, serving CONTEXT_ROOT"
	@echo "  serve-http  same, over HTTP on 127.0.0.1:8000/mcp, unauthenticated"
	@echo "  serve-http-demo  over HTTP, authenticated with the demo identities"
	@echo "  serve-as    over stdio as a demo identity, with enforcement on"
	@echo "  demo        walk the acceptance demo end to end against CONTEXT_ROOT"
	@echo "  inspector   serve over HTTP and open MCP Inspector against it"
	@echo "  usage       summarise logs/usage.jsonl: what was looked up, and what was missed"
	@echo "  dashboard   serve the usage dashboard on :8010, reading the log live"
	@echo ""
	@echo "CONTEXT_ROOT defaults to ../ki-ccl/dist/staging (run 'make package' there"
	@echo "first). Override with CONTEXT_ROOT=/path/to/tree for any other packaged tree."

install:
	uv venv .venv --allow-existing
	uv pip install -q -r requirements-dev.txt

test:
	$(PY) -m pytest -q

serve:
	CONTEXT_ROOT=$(CONTEXT_ROOT) $(PY) server/mcp_server.py

# KI_ICL_AUTH=off says out loud that this port is unauthenticated. The server refuses
# to start over HTTP without one of entra/demo/off, so a forgotten variable is a server
# that does not come up rather than one that serves everything to anyone.
serve-http:
	CONTEXT_ROOT=$(CONTEXT_ROOT) KI_ICL_AUTH=off $(PY) server/mcp_server.py --http

# Authenticated with the fake identities in config/demo_principals.yaml. Pass a token as
# `Authorization: Bearer demo-token-<id>`; see that file for what each one proves.
#
# The fallback audit key is committed on purpose and is NOT a secret: it exists so the
# pseudonyms in a local run are stable and comparable. It must never reach a deployment.
# Anyone with this repo can dictionary-attack pseudonyms minted with it, which is the
# whole reason server/identity.py insists the key be secret. Demo mode refuses to bind
# off loopback, so this target cannot serve anything but this machine.
serve-http-demo:
	CONTEXT_ROOT=$(CONTEXT_ROOT) KI_ICL_AUTH=demo KI_ICL_ENFORCE=1 \
	  KI_ICL_AUDIT_KEY=$${KI_ICL_AUDIT_KEY:-local-demo-key-not-a-secret-0123456789} \
	  $(PY) server/mcp_server.py --http

# Enforcement over stdio, by adopting a demo identity. The only way to see filtering
# locally without HTTP, since stdio carries no token and so observes by default.
# DEV_PRINCIPAL=baseline make serve-as
DEV_PRINCIPAL ?= baseline

serve-as:
	CONTEXT_ROOT=$(CONTEXT_ROOT) KI_ICL_DEV_PRINCIPAL=$(DEV_PRINCIPAL) KI_ICL_ENFORCE=1 \
	  $(PY) server/mcp_server.py

# Ports are overridable because the Inspector's defaults (6274/6277) collide with
# any other Inspector already running, and it fails rather than falling back.
INSPECTOR_CLIENT_PORT ?= 6374
INSPECTOR_SERVER_PORT ?= 6377

inspector:
	@echo "Serving http://127.0.0.1:8000/mcp"
	@CONTEXT_ROOT=$(CONTEXT_ROOT) $(PY) server/mcp_server.py --http & \
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
	@CONTEXT_ROOT=$(CONTEXT_ROOT) DASHBOARD_PORT=$(DASHBOARD_PORT) $(PY) scripts/dashboard.py

demo:
	CONTEXT_ROOT=$(CONTEXT_ROOT) $(PY) scripts/demo.py

.PHONY: help install validate package test serve serve-http demo clean

PY := .venv/bin/python

help:
	@echo "Targets:"
	@echo "  install     create .venv and install dependencies"
	@echo "  validate    run the structural validation gate over domains/"
	@echo "  package     build dist/context/ (upload dir) and dist/staging/ (servable tree)"
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

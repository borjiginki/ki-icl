.PHONY: help install validate package test serve serve-http demo clean

PY := .venv/bin/python

help:
	@echo "Targets:"
	@echo "  install     create .venv and install dependencies"
	@echo "  validate    run the structural validation gate over domains/"
	@echo "  package     build dist/context/ (upload dir) and dist/staging/ (servable tree)"
	@echo "  test        run the test suite"
	@echo "  serve       run the MCP server over stdio, serving dist/staging"
	@echo "  serve-http  same, over HTTP on 127.0.0.1:8000/mcp"
	@echo "  demo        package, then walk the acceptance demo end to end"
	@echo "  inspector   serve over HTTP and open MCP Inspector against it"
	@echo "  usage       summarise logs/usage.jsonl: what was looked up, and what was missed"
	@echo "  dashboard   serve the usage dashboard on :8010, reading the log live"

install:
	uv venv .venv --allow-existing
	uv pip install -q -r requirements-dev.txt

validate:
	$(PY) scripts/validate_context.py

package: validate
	$(PY) scripts/package_context.py

test:
	$(PY) -m pytest -q

serve: package
	CONTEXT_ROOT=dist/staging $(PY) server/mcp_server.py

serve-http: package
	CONTEXT_ROOT=dist/staging $(PY) server/mcp_server.py --http

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
	@DASHBOARD_PORT=$(DASHBOARD_PORT) $(PY) scripts/dashboard.py

demo: package
	CONTEXT_ROOT=dist/staging $(PY) scripts/demo.py

clean:
	rm -rf dist

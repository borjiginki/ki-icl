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

serve-http: package
	CONTEXT_ROOT=dist/staging $(PY) server/mcp_server.py --http

inspector: package
	@echo "Serving http://127.0.0.1:8000/mcp -- opening MCP Inspector..."
	@CONTEXT_ROOT=dist/staging $(PY) server/mcp_server.py --http & \
	 sleep 2; npx -y @modelcontextprotocol/inspector; kill %1

usage:
	@$(PY) scripts/usage_report.py

demo: package
	CONTEXT_ROOT=dist/staging $(PY) scripts/demo.py

clean:
	rm -rf dist

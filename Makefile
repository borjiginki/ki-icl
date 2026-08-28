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

demo: package
	CONTEXT_ROOT=dist/staging $(PY) scripts/demo.py

clean:
	rm -rf dist

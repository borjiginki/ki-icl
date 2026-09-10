# Dashboard on the Server Ingress Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the usage dashboard reachable at `https://<fqdn>/dashboard` on the deployed MCP server's existing ingress and IP allow-list, behind a switch that is off by default.

**Architecture:** The pure aggregation in `scripts/dashboard.py` moves to `server/dashboard.py` so the image can import it, and the script becomes a thin loopback runner over the same functions. The server mounts four routes through FastMCP's `custom_route`, on the port and ingress that already serve `/mcp`, only when the composition root passes `dashboard=True`. Records come from the container's own writable layer by switching the existing file sink back on alongside stderr, so Log Analytics remains the audit store and no new Azure resource is created.

**Tech Stack:** Python 3.12, FastMCP 3.4.7 (pinned `>=2.10,<4`), Starlette, pytest, Terraform (azurerm), GitHub Actions, Azure Container Apps.

**Spec:** [docs/superpowers/specs/2026-09-10-dashboard-on-the-server-ingress-design.md](../specs/2026-09-10-dashboard-on-the-server-ingress-design.md)

## Global Constraints

- The default posture must not change. With the switch unset, the route table, the environment and the deployment are bit-for-bit what ships today.
- The switch is the environment variable `KI_ICL_DASHBOARD`, and it is on only when its stripped value is exactly `"1"`, matching `KI_ICL_AUDIT_REQUIRED`.
- `server/mcp_server.py`'s `build_server` reads no environment. The composition root reads it and passes a flag, exactly as it already does for `auth`.
- `server/dashboard.py` must be added to `AGGREGATORS` in `tests/test_purpose_limitation.py`. That tuple is the control that makes the purpose limitation real, and code moved out from under it is a control silently retired.
- No new Azure resource, no second Files share, no second Container App, no change to where the audit log lives.
- The deployed path is `/dashboard`, with `/dashboard/data`, `/dashboard/curate` and `/dashboard/purge` beneath it. The loopback script keeps serving `/`, `/data`, `/curate` and `/purge`.
- Terraform variable name: `dashboard_enabled`, `bool`, default `false`. Repository variable name: `DASHBOARD_ENABLED`.
- No em dash characters in any prose written by this plan.
- Run the full suite with `.venv/bin/python -m pytest -q` from the repository root. Single tests with `.venv/bin/python -m pytest <path>::<name> -v`.

---

## File Structure

| File | Responsibility |
|---|---|
| `server/dashboard.py` (new) | Every pure function the dashboard needs, plus `register(mcp)` which mounts the four routes. Importable from inside the image, which copies only `server/`. |
| `scripts/dashboard.py` (shrinks) | The loopback host only: `Handler`, `main`, `PORT`. Imports everything else from `server.dashboard`. |
| `server/dashboard.html` (edit) | Addresses its endpoints relative to wherever it is served from. |
| `server/mcp_server.py` (edit) | `dashboard_from_env()`, the `dashboard` parameter on `build_server`, and the two startup guards. |
| `deploy/variables.tf`, `deploy/app.tf`, `deploy/outputs.tf` (edit) | The `dashboard_enabled` variable, the two environment variables, three preconditions, the URL output. |
| `.github/workflows/deploy.yml` (edit) | Declares the switch from repository settings, and passes `max_replicas=1` with it. |
| `tests/test_dashboard_routes.py` (new) | The route table, the four routes' behaviour, and the guards. |
| `tests/test_dashboard.py`, `tests/test_curation.py`, `tests/test_purge.py`, `tests/test_purpose_limitation.py` (edit) | Follow the move. |

---

### Task 1: Move the aggregation into `server/dashboard.py`

Behaviour-preserving. The 878 lines of existing dashboard tests passing with nothing changed but their import line is the evidence.

**Files:**
- Create: `server/dashboard.py`
- Modify: `scripts/dashboard.py` (becomes the loopback host only)
- Test: `tests/test_purpose_limitation.py:25`, `tests/test_dashboard.py:18`, `tests/test_curation.py:29`, `tests/test_purge.py:23`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: module `server.dashboard` exporting `LOG: Path`, `CURATION: Path`, `PAGE: Path`, `CURATION_STATES: set[str]`, `read_records(path: Path) -> list[dict]`, `read_curation(path: Path) -> dict`, `curate(path: Path, key: str, state: str, count: int = 0) -> dict`, `purge(log_path: Path, curation_path: Path, key: str) -> dict`, `filter_records(records, *, domain: str = "", hours: float = 0, now: str = "") -> list[dict]`, `aggregate(records, curation=None, catalog=None) -> dict`, `live_catalog() -> dict | None`, and the private helpers they already use.

- [ ] **Step 1: Extend the purpose-limitation guard to the new file**

The guard is written first on purpose. It is the control that must survive the move, and right now it names a file that does not exist yet, so it fails.

In `tests/test_purpose_limitation.py`, replace line 25:

```python
AGGREGATORS = ("scripts/dashboard.py", "scripts/usage_report.py")
```

with:

```python
# server/dashboard.py is here because the aggregation moved there so the image could
# import it. A file list is a fragile control exactly when code moves, and moving
# aggregation out from under this tuple would retire the control while still showing
# green. See docs/superpowers/specs/2026-09-10-dashboard-on-the-server-ingress-design.md.
AGGREGATORS = ("scripts/dashboard.py", "scripts/usage_report.py", "server/dashboard.py")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_purpose_limitation.py -v`
Expected: FAIL. Both parametrised cases for `server/dashboard.py` raise `FileNotFoundError` because the file does not exist yet.

- [ ] **Step 3: Create `server/dashboard.py` by moving code out of `scripts/dashboard.py`**

Copy `scripts/dashboard.py` to `server/dashboard.py`, then in the new file delete `class Handler`, `def main`, the `if __name__ == "__main__":` block, the `PORT` constant, the `sys.path.insert` line, and the now-unused imports `sys`, `BaseHTTPRequestHandler`, `ThreadingHTTPServer`, `parse_qs`, `urlparse`.

Everything else moves unchanged: `ROOT`, `LOG`, `PAGE`, `CURATION`, `CURATION_STATES`, `_RETURNS_ON_DEMAND`, `read_records`, `read_curation`, `_write_curation`, `curate`, `_is_demand_for`, `purge`, `filter_records`, `_utcnow`, `_key`, `_pct`, `aggregate`, `_kpi`, `_misses`, `_served`, `_funnel`, `_offered`, `_activity`, `_versions`, `_latency`, `live_catalog`.

`ROOT = Path(__file__).resolve().parent.parent` stays as it is and still resolves to the repository root, because `server/` is one level below it just as `scripts/` is. In the container that makes `ROOT` equal `/app`.

Keep the imports inside `live_catalog` local rather than hoisting them to the top of the file. The `try`/`except` around them is what lets the dashboard render when the tree cannot be read, and a top-level import would turn that into an import error at startup.

Replace the module docstring with:

```python
"""Everything the usage dashboard computes, as pure functions over a list of records.

Two hosts serve these. `scripts/dashboard.py` binds loopback for local work, and
`register` below mounts the same data on the MCP server's own ingress when the
composition root asks for it. Neither host is authenticated, and `live_catalog`
deliberately reports the whole corpus regardless of grants, so what stands in front of
this is the entire gate. See the `register` docstring.

This lives under `server/` rather than `scripts/` for one reason: the image copies only
`server/`, and the deployed host has to import it.

POC scaffolding. In production the records go to stdout and Log Analytics does this
job, so this is meant to be deleted rather than ported to ki-mcp.
"""
```

- [ ] **Step 4: Reduce `scripts/dashboard.py` to the loopback host**

Replace everything above `class Handler` with:

```python
#!/usr/bin/env python3
"""Serve the usage dashboard on loopback. `make dashboard`.

Reads `logs/usage.jsonl` on every poll, so records appear while you test. Needs no
MCP server running: it reads the file, not the server.

This file is the loopback host and nothing else. Every function it calls lives in
`server/dashboard.py`, shared with the host the MCP server mounts at /dashboard, so the
two cannot drift into disagreeing about what a number means.
"""

from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# Run directly (`python3 scripts/dashboard.py`) and only scripts/ lands on sys.path, so
# `server.*` is unimportable without this. Proven by
# test_the_dashboard_serves_a_live_catalog_when_run_as_a_script, which launches this the
# way the Makefile does; nothing in-process catches it, because pytest puts the
# repository root on the path itself.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.dashboard import (  # noqa: E402
    CURATION,
    CURATION_STATES,
    LOG,
    PAGE,
    aggregate,
    curate,
    filter_records,
    live_catalog,
    purge,
    read_curation,
    read_records,
)

PORT = int(os.environ.get("DASHBOARD_PORT", "8010"))
```

Leave `class Handler`, `def main` and the `if __name__ == "__main__":` block exactly as they are.

- [ ] **Step 5: Point the three existing test modules at the new home**

`tests/test_dashboard.py:18`:

```python
from server.dashboard import aggregate, filter_records, read_records  # noqa: E402
```

`tests/test_curation.py:29`:

```python
from server.dashboard import aggregate, curate, read_curation  # noqa: E402
```

`tests/test_purge.py:23`:

```python
from server.dashboard import (  # noqa: E402
    aggregate,
    curate,
    purge,
    read_curation,
    read_records,
)
```

Change nothing else in those files. Their passing unmodified is the proof this task moved code rather than rewrote it.

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS, with the same test count as before plus the two new parametrised cases in `test_purpose_limitation.py`.

If `test_the_dashboard_serves_a_live_catalog_when_run_as_a_script` skips, that is a missing `ki-ccl` sibling checkout, not a failure. Run it deliberately at least once with a real checkout present, because it is the only test that exercises the `sys.path` line in step 4:

Run: `.venv/bin/python -m pytest tests/test_dashboard.py::test_the_dashboard_serves_a_live_catalog_when_run_as_a_script -v`

- [ ] **Step 7: Verify the loopback dashboard by hand**

Run: `make dashboard` and open `http://127.0.0.1:8010`.
Expected: the page renders exactly as it did before this task, with panels populated from `logs/usage.jsonl`. Stop it with Ctrl-C.

- [ ] **Step 8: Commit**

```bash
git add server/dashboard.py scripts/dashboard.py tests/test_dashboard.py tests/test_curation.py tests/test_purge.py tests/test_purpose_limitation.py
git commit -m "Move the dashboard's aggregation into server/, where the image can import it"
```

---

### Task 2: Address the page's endpoints relative to where it is served

**Files:**
- Modify: `server/dashboard.html:213` (insert), `:386`, `:586`
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: nothing.
- Produces: a page that works at `/` and at `/dashboard`. Task 3 depends on this and will serve a broken page without it.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_dashboard.py`:

```python
def test_the_page_addresses_its_endpoints_relative_to_where_it_is_served():
    """Served at / by scripts/dashboard.py and at /dashboard by the MCP server.

    An absolute path works only for the first, and the failure mode is quiet: the page
    renders its whole layout and then shows no data, because the fetch 404'd against a
    route that does not exist under that prefix.
    """
    page = (Path(__file__).resolve().parent.parent / "server" / "dashboard.html").read_text(
        encoding="utf-8"
    )

    assert 'const BASE = location.pathname.replace(/\\/+$/, "");' in page
    assert 'fetch(BASE + "/data?" + q)' in page
    assert 'fetch(BASE + (state === "purge" ? "/purge" : "/curate")' in page
    assert 'fetch("/' not in page, "an absolute fetch cannot work under a path prefix"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_dashboard.py::test_the_page_addresses_its_endpoints_relative_to_where_it_is_served -v`
Expected: FAIL on the first assertion, because `BASE` does not exist yet.

- [ ] **Step 3: Add the constant**

In `server/dashboard.html`, immediately after the opening `<script>` on line 213:

```js
/* Every request below is relative to wherever this page is served from: `/` under
   scripts/dashboard.py, `/dashboard` under the MCP server's own ingress. Absolute
   paths worked only for the first of those, and the second failure is silent. */
const BASE = location.pathname.replace(/\/+$/, "");
```

- [ ] **Step 4: Use it at both call sites**

Line 386, from:

```js
      await fetch(state === "purge" ? "/purge" : "/curate", {
```

to:

```js
      await fetch(BASE + (state === "purge" ? "/purge" : "/curate"), {
```

Line 586, from:

```js
    render(await (await fetch("/data?" + q)).json());
```

to:

```js
    render(await (await fetch(BASE + "/data?" + q)).json());
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_dashboard.py -v`
Expected: PASS.

- [ ] **Step 6: Verify the loopback page still works by hand**

Run: `make dashboard`, open `http://127.0.0.1:8010`, and confirm the panels still populate and the dismiss button on a suggestion still works. `BASE` is `""` at `/`, so this must behave exactly as before. Stop with Ctrl-C.

- [ ] **Step 7: Commit**

```bash
git add server/dashboard.html tests/test_dashboard.py
git commit -m "Let the dashboard page work under a path prefix, not only at the root"
```

---

### Task 3: Mount the four routes on the server

**Files:**
- Modify: `server/dashboard.py` (add `query_window`, `demand_baseline`, `register`)
- Modify: `scripts/dashboard.py` (`Handler` uses the two new shared helpers)
- Modify: `server/mcp_server.py` (`dashboard_from_env`, the `dashboard` parameter, the module-level call)
- Modify: `Makefile`
- Test: `tests/test_dashboard_routes.py` (new)

**Interfaces:**
- Consumes: `server.dashboard`'s exports from Task 1; the base-relative page from Task 2.
- Produces: `server.dashboard.register(mcp) -> None`, `server.dashboard.query_window(domain: str = "", hours: str = "") -> dict[str, Any]`, `server.dashboard.demand_baseline(key: str) -> int`, `server.mcp_server.dashboard_from_env() -> bool`, and `build_server(auth: AuthProvider | None = None, dashboard: bool = False) -> FastMCP`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dashboard_routes.py`:

```python
"""The dashboard mounted on the MCP server's own ingress.

Off unless asked for, and asserted against the route table rather than a 404, because
a future catch-all route would make a 404 assertion pass for entirely the wrong reason.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import dashboard, mcp_server  # noqa: E402

DASHBOARD_PATHS = {"/dashboard", "/dashboard/data", "/dashboard/curate", "/dashboard/purge"}


def paths(app) -> set[str]:
    return {getattr(route, "path", "") for route in app.routes}


@pytest.fixture
def log(tmp_path, monkeypatch) -> Path:
    """A usage log with one miss in it, and a curation file beside it."""
    path = tmp_path / "usage.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(record)
            for record in [
                {
                    "ts": "2026-09-10T08:00:00.000Z",
                    "event": "context_use",
                    "session": "aaa",
                    "tool": "get_artifact",
                    "domain": "method",
                    "id": "nope",
                    "outcome": "not_found",
                },
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(dashboard, "LOG", path)
    monkeypatch.setattr(dashboard, "CURATION", tmp_path / "curation.json")
    return path


def test_no_dashboard_routes_unless_asked():
    """The default posture is the one that ships. Nothing mounted, not even a 404 route."""
    app = mcp_server.build_server().http_app()

    assert not (paths(app) & DASHBOARD_PATHS)
    assert "/mcp" in paths(app)


def test_the_dashboard_mounts_when_asked():
    app = mcp_server.build_server(dashboard=True).http_app()

    assert DASHBOARD_PATHS <= paths(app)


def test_the_page_is_served_at_the_dashboard_path(log):
    with TestClient(mcp_server.build_server(dashboard=True).http_app()) as client:
        response = client.get("/dashboard")

    assert response.status_code == 200
    assert "ki-icl context usage" in response.text


def test_the_data_route_returns_what_aggregate_returns(log):
    with TestClient(mcp_server.build_server(dashboard=True).http_app()) as client:
        payload = client.get("/dashboard/data").json()

    assert payload["misses"][0]["key"] == "method/nope"
    assert payload["record_count"] == 1


def test_the_data_route_honours_the_domain_filter(log):
    with TestClient(mcp_server.build_server(dashboard=True).http_app()) as client:
        payload = client.get("/dashboard/data?domain=company").json()

    assert payload["misses"] == []


def test_a_nonsense_hours_value_is_no_window_rather_than_a_traceback(log):
    """It arrives from a URL somebody typed, and this endpoint is reachable from an
    allow-listed address, so a 500 with a traceback is the wrong answer."""
    with TestClient(mcp_server.build_server(dashboard=True).http_app()) as client:
        response = client.get("/dashboard/data?hours=soon")

    assert response.status_code == 200
    assert response.json()["record_count"] == 1


def test_curate_marks_a_suggestion(log, tmp_path):
    with TestClient(mcp_server.build_server(dashboard=True).http_app()) as client:
        response = client.post(
            "/dashboard/curate", json={"key": "method/nope", "state": "dismissed"}
        )

    assert response.status_code == 200
    entry = json.loads((tmp_path / "curation.json").read_text(encoding="utf-8"))["entries"][0]
    assert entry["key"] == "method/nope"
    assert entry["state"] == "dismissed"
    # The baseline is the demand that was on screen, computed here rather than taken
    # from the client, so a stale page cannot record one that never existed.
    assert entry["count"] == 1


def test_curate_refuses_an_unknown_state(log):
    with TestClient(mcp_server.build_server(dashboard=True).http_app()) as client:
        response = client.post(
            "/dashboard/curate", json={"key": "method/nope", "state": "banished"}
        )

    assert response.status_code == 400


def test_curate_refuses_a_missing_key(log):
    with TestClient(mcp_server.build_server(dashboard=True).http_app()) as client:
        response = client.post("/dashboard/curate", json={"state": "dismissed"})

    assert response.status_code == 400


def test_purge_removes_the_records_that_produced_the_suggestion(log):
    with TestClient(mcp_server.build_server(dashboard=True).http_app()) as client:
        response = client.post("/dashboard/purge", json={"key": "method/nope"})

    assert response.status_code == 200
    assert response.json() == {"key": "method/nope", "removed": 1}
    remaining = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert [r["event"] for r in remaining] == ["purge"]


def test_purge_refuses_a_missing_key(log):
    with TestClient(mcp_server.build_server(dashboard=True).http_app()) as client:
        response = client.post("/dashboard/purge", json={})

    assert response.status_code == 400
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_dashboard_routes.py -v`
Expected: FAIL. `test_no_dashboard_routes_unless_asked` passes trivially; every other test fails, the `dashboard=True` ones with `TypeError: build_server() got an unexpected keyword argument 'dashboard'`.

- [ ] **Step 3: Add the two shared helpers to `server/dashboard.py`**

Both hosts need them, and sharing them is what stops the two hosts drifting. Add after `filter_records`:

```python
def query_window(domain: str = "", hours: str = "") -> dict[str, Any]:
    """The two filter arguments `filter_records` takes, from raw query strings.

    A non-numeric `hours` means no window rather than an exception. It arrives from a
    URL somebody typed, and on the deployed host an unhandled one is a 500 with a
    traceback on an endpoint reachable from the allow-list.
    """
    try:
        window = float(hours or 0)
    except ValueError:
        window = 0.0
    return {"domain": domain, "hours": window}


def demand_baseline(key: str) -> int:
    """The demand behind `key` right now, for `curate` to record as its baseline.

    Computed from the log rather than taken from the request, because the baseline is
    meant to be the demand the person was actually looking at when they decided. A
    client-supplied number would let a stale page record one that was never on screen.
    """
    current = aggregate(read_records(LOG), curation=read_curation(CURATION))
    counts = {row["key"]: row["count"] for row in current["misses"] + current["curated"]}
    return counts.get(key, 0)
```

- [ ] **Step 4: Add `register` to `server/dashboard.py`**

Append at the end of the file:

```python
def register(mcp: Any) -> None:
    """Mount the dashboard on an existing FastMCP server, at /dashboard.

    **These routes are not access-controlled, and two of them are worse than that.**
    `/dashboard/data` carries `live_catalog`, which lists every artifact id in the
    corpus regardless of who may read it, and `/dashboard/purge` rewrites the usage
    log. Whatever ingress this sits behind is the entire gate, which is why the
    composition root has to ask for it explicitly, why `refuse_unsafe_start` refuses it
    in entra mode, and why deploy/variables.tf defaults it off.

    Starlette is imported here rather than at module scope so `scripts/dashboard.py`,
    which needs none of it, keeps its stdlib-only import list.
    """
    from starlette.requests import Request
    from starlette.responses import HTMLResponse, JSONResponse, Response

    def _body_key_and_state(raw: bytes) -> tuple[str, str] | None:
        """`(key, state)` from a request body, or None for anything unparseable."""
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            return None
        if not isinstance(body, dict):
            return None
        return str(body.get("key", "")), str(body.get("state", ""))

    @mcp.custom_route("/dashboard", methods=["GET"], include_in_schema=False)
    async def page(request: Request) -> Response:
        return HTMLResponse(PAGE.read_bytes(), headers={"Cache-Control": "no-store"})

    @mcp.custom_route("/dashboard/data", methods=["GET"], include_in_schema=False)
    async def data(request: Request) -> Response:
        records = filter_records(
            read_records(LOG),
            **query_window(
                domain=request.query_params.get("domain", ""),
                hours=request.query_params.get("hours", ""),
            ),
        )
        return JSONResponse(
            aggregate(records, curation=read_curation(CURATION), catalog=live_catalog()),
            headers={"Cache-Control": "no-store"},
        )

    @mcp.custom_route("/dashboard/curate", methods=["POST"], include_in_schema=False)
    async def curate_route(request: Request) -> Response:
        parsed = _body_key_and_state(await request.body())
        if parsed is None:
            return Response(status_code=400)
        key, state = parsed
        if not key or state not in CURATION_STATES:
            return Response(status_code=400)
        return JSONResponse(curate(CURATION, key, state, demand_baseline(key)))

    @mcp.custom_route("/dashboard/purge", methods=["POST"], include_in_schema=False)
    async def purge_route(request: Request) -> Response:
        parsed = _body_key_and_state(await request.body())
        if parsed is None:
            return Response(status_code=400)
        key, _ = parsed
        if not key:
            return Response(status_code=400)
        return JSONResponse(purge(LOG, CURATION, key))
```

- [ ] **Step 5: Have the loopback host use the same two helpers**

In `scripts/dashboard.py`, add `demand_baseline` and `query_window` to the `from server.dashboard import (...)` list, keeping it alphabetical.

In `Handler.do_GET`, replace:

```python
            query = parse_qs(urlparse(self.path).query)
            records = filter_records(
                read_records(LOG),
                domain=query.get("domain", [""])[0],
                hours=float(query.get("hours", ["0"])[0] or 0),
            )
```

with:

```python
            query = parse_qs(urlparse(self.path).query)
            records = filter_records(
                read_records(LOG),
                **query_window(
                    domain=query.get("domain", [""])[0],
                    hours=query.get("hours", [""])[0],
                ),
            )
```

In `Handler.do_POST`, replace:

```python
        # Baseline computed here, not taken from the client: it is the demand the
        # person was actually looking at when they made the decision.
        current = aggregate(read_records(LOG), curation=read_curation(CURATION))
        counts = {r["key"]: r["count"] for r in current["misses"] + current["curated"]}
        self._send(
            json.dumps(curate(CURATION, key, state, counts.get(key, 0))).encode(),
            "application/json",
        )
```

with:

```python
        self._send(
            json.dumps(curate(CURATION, key, state, demand_baseline(key))).encode(),
            "application/json",
        )
```

Then remove `aggregate` and `read_curation` from the import list if nothing else in the file uses them. Check with `grep -n "aggregate\|read_curation" scripts/dashboard.py` before removing.

- [ ] **Step 6: Wire it into the composition root**

In `server/mcp_server.py`, add to the imports at the top of the file:

```python
from server.dashboard import register as register_dashboard
```

Imported under an alias so the module name does not shadow `build_server`'s `dashboard` parameter.

Change the signature on line 128:

```python
def build_server(auth: AuthProvider | None = None, dashboard: bool = False) -> FastMCP:
```

and extend its docstring with a paragraph:

```
    `dashboard` mounts the usage dashboard at /dashboard, on this same app and port.
    A parameter rather than an environment read, for the same reason `auth` is one:
    this function reads no environment, the composition root decides, and a test can
    mount the routes without touching `os.environ`.
```

Replace the final `return mcp` on line 237 with:

```python
    if dashboard:
        register_dashboard(mcp)

    return mcp
```

Add beside `auth_mode()`, after it:

```python
def dashboard_from_env() -> bool:
    """Whether to mount the usage dashboard. Off unless explicitly switched on.

    Exactly "1", like KI_ICL_AUDIT_REQUIRED, rather than any truthy-looking string.
    This one decides whether an unauthenticated view of the whole corpus index is
    reachable, so "true", "yes" and "0 " should all fail closed rather than be guessed
    at.
    """
    return os.environ.get("KI_ICL_DASHBOARD", "").strip() == "1"
```

Change the module-level construction on line 222:

```python
mcp = build_server(auth=auth_from_env(), dashboard=dashboard_from_env())
```

- [ ] **Step 7: Add a Makefile target for the deployed shape**

In `Makefile`, add `serve-http-dashboard` to the `.PHONY` list on line 1, add a help line after the `serve-http-demo` one:

```make
	@echo "  serve-http-dashboard  same, with the usage dashboard at /dashboard"
```

and add the target after `serve-http`:

```make
# The dashboard on the server's own port, mounted exactly as the deployment mounts it,
# so what you check locally is what the allow-list reaches. CONTEXT_USAGE_LOG is set
# because the server defaults the file sink on but the deployment does not: this target
# exists to match the deployed shape, so it says so rather than relying on the default.
serve-http-dashboard:
	CONTEXT_ROOT=$(CONTEXT_ROOT) KI_ICL_AUTH=off KI_ICL_DASHBOARD=1 \
	  CONTEXT_USAGE_LOG=logs/usage.jsonl $(PY) server/mcp_server.py --http
```

- [ ] **Step 8: Run the new tests**

Run: `.venv/bin/python -m pytest tests/test_dashboard_routes.py -v`
Expected: PASS, all twelve.

- [ ] **Step 9: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS. `tests/test_purpose_limitation.py` in particular must still pass over the enlarged `server/dashboard.py`.

- [ ] **Step 10: Verify both hosts by hand**

Run: `make serve-http-dashboard`, then in a second terminal exercise the server so there is something to draw:

```bash
CONTEXT_ROOT=../ki-ccl/dist/staging .venv/bin/python scripts/demo.py
```

Open `http://127.0.0.1:8000/dashboard`.
Expected: the same page `make dashboard` serves, with panels populated, the header count rising as you re-run the demo, and the domain and range selectors filtering. Click dismiss on a suggestion and confirm the row moves to the curated list. Look at it properly: check the panels line up, nothing overflows its card, and the dark and light renderings both hold.

Then run `make dashboard` in a third terminal and confirm `http://127.0.0.1:8010` still renders the same numbers from the same file. Stop both.

- [ ] **Step 11: Commit**

```bash
git add server/dashboard.py scripts/dashboard.py server/mcp_server.py Makefile tests/test_dashboard_routes.py
git commit -m "Mount the usage dashboard on the server's own ingress, off by default"
```

---

### Task 4: The two guards, in the server

Terraform can only refuse a deployment it is asked to make. `az containerapp update` is not asked to make one.

**Files:**
- Modify: `server/mcp_server.py` (`refuse_unsafe_start`, `startup_lines`)
- Test: `tests/test_http_auth.py`

**Interfaces:**
- Consumes: `dashboard_from_env()` from Task 3.
- Produces: no new names. `refuse_unsafe_start` raises `SystemExit` on one more configuration, and `startup_lines` emits one more line.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_http_auth.py`:

```python
def test_the_dashboard_is_refused_in_entra_mode(served, monkeypatch):
    """It has no authentication of its own and its catalog panel lists every artifact
    id regardless of grants, so an IP allow-list stops being a defensible gate the
    moment there are real identities to gate. Terraform refuses this too; this is the
    copy that survives an `az containerapp update`."""
    from server import mcp_server

    monkeypatch.setenv("KI_ICL_AUTH", "entra")
    monkeypatch.setenv("KI_ICL_DASHBOARD", "1")

    with pytest.raises(SystemExit, match="KI_ICL_DASHBOARD"):
        mcp_server.refuse_unsafe_start(http=True, host="127.0.0.1")


def test_the_dashboard_is_allowed_alongside_unauthenticated_serving(served, monkeypatch):
    """`off` is the disclosed posture the demo runs in. The guard is about entra, not
    about the dashboard being unwelcome."""
    from server import mcp_server

    monkeypatch.setenv("KI_ICL_AUTH", "off")
    monkeypatch.setenv("KI_ICL_DASHBOARD", "1")

    mcp_server.refuse_unsafe_start(http=True, host="0.0.0.0")


def test_a_dashboard_on_a_wide_bind_is_announced_loudly(monkeypatch):
    from server import access, mcp_server

    monkeypatch.setenv("KI_ICL_AUTH", "off")
    monkeypatch.setenv("KI_ICL_DASHBOARD", "1")

    lines = mcp_server.startup_lines(
        host="0.0.0.0", port=8000, http=True, mode=access.Mode.OBSERVE
    )
    text = " ".join(lines)

    assert "/dashboard" in text
    assert "purge" in text


def test_no_dashboard_warning_when_it_is_not_mounted(monkeypatch):
    """A warning that fires when it does not apply becomes noise and stops being read."""
    from server import access, mcp_server

    monkeypatch.setenv("KI_ICL_AUTH", "off")
    monkeypatch.delenv("KI_ICL_DASHBOARD", raising=False)

    lines = mcp_server.startup_lines(
        host="0.0.0.0", port=8000, http=True, mode=access.Mode.OBSERVE
    )

    assert "/dashboard" not in " ".join(lines)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_http_auth.py -k dashboard -v`
Expected: FAIL. `test_the_dashboard_is_refused_in_entra_mode` fails because nothing raises, and the two `startup_lines` tests fail on the missing text. `test_the_dashboard_is_allowed_alongside_unauthenticated_serving` passes already, which is correct: it is the guard against over-refusing.

- [ ] **Step 3: Add the refusal**

In `server/mcp_server.py`, in `refuse_unsafe_start`, directly after the existing `KI_ICL_DEV_PRINCIPAL` check:

```python
    if mode == "entra" and dashboard_from_env():
        raise SystemExit(
            "KI_ICL_DASHBOARD is refused in entra mode. The dashboard has no "
            "authentication of its own, and its catalog panel lists every artifact id "
            "in the corpus regardless of grants, so it must not be reachable once "
            "there are real identities to gate. deploy/variables.tf refuses the same "
            "combination; this is the copy that survives an out-of-band update."
        )
```

- [ ] **Step 4: Add the warning**

In `startup_lines`, directly after the existing `if http and auth_mode() == "off" and host not in LOOPBACK:` block:

```python
    if http and dashboard_from_env() and host not in LOOPBACK:
        lines.append(
            f"WARNING: the usage dashboard is mounted at http://{host}:{port}/dashboard. "
            f"It lists every artifact id in the corpus regardless of grants, and "
            f"/dashboard/purge rewrites the usage log. It has no authentication of its "
            f"own; only the surrounding network is stopping anyone who can reach this "
            f"port."
        )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_http_auth.py -v`
Expected: PASS.

- [ ] **Step 6: Verify the refusal by hand**

```bash
KI_ICL_AUTH=entra KI_ICL_DASHBOARD=1 CONTEXT_ROOT=../ki-ccl/dist/staging \
  .venv/bin/python server/mcp_server.py --http
```

Expected: exits immediately with the `KI_ICL_DASHBOARD is refused in entra mode` message, before binding anything.

```bash
KI_ICL_HOST=0.0.0.0 KI_ICL_AUTH=off KI_ICL_DASHBOARD=1 CONTEXT_ROOT=../ki-ccl/dist/staging \
  .venv/bin/python server/mcp_server.py --http
```

Expected: starts, and the banner on stderr carries both the existing UNAUTHENTICATED warning and the new dashboard one. Stop with Ctrl-C.

- [ ] **Step 7: Run the full suite and commit**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS.

```bash
git add server/mcp_server.py tests/test_http_auth.py
git commit -m "Refuse the dashboard in entra mode, and announce it on a wide bind"
```

---

### Task 5: Terraform and the Deploy pipeline

**Files:**
- Modify: `deploy/variables.tf`, `deploy/app.tf:189-194` and the `lifecycle` block at `:252`, `deploy/outputs.tf`
- Modify: `.github/workflows/deploy.yml` (the `terraform plan` step)
- Test: `terraform fmt -check` and `terraform validate` in `deploy/`. The preconditions themselves are only evaluated during a real plan, which needs Azure credentials and the remote backend, so CI's plan job is where they are proven.

**Interfaces:**
- Consumes: `KI_ICL_DASHBOARD` and `CONTEXT_USAGE_LOG` as read by Tasks 3 and 4.
- Produces: Terraform variable `dashboard_enabled`, output `dashboard_url`, repository variable `DASHBOARD_ENABLED`.

- [ ] **Step 1: Add the variable**

In `deploy/variables.tf`, immediately after the `allowed_client_cidrs` block that ends on line 198:

```hcl
variable "dashboard_enabled" {
  description = <<-EOT
    Whether the server also mounts the usage dashboard at /dashboard, on the same port
    and behind the same ingress and allow-list as /mcp.

    Demo scaffolding, off by default and deliberately hard to leave on. The dashboard
    has no authentication of its own, its catalog panel lists every artifact id in the
    corpus regardless of grants, and /dashboard/purge rewrites the usage log, so the IP
    allow-list in allowed_client_cidrs is the entire gate. The preconditions on the
    container app refuse it without external ingress, refuse it above one replica, and
    refuse it outright once auth_mode = "entra"; the server carries its own copy of that
    last one, so an out-of-band update cannot get around it either.

    Turning this on also sets CONTEXT_USAGE_LOG to a path, which switches the file sink
    back on alongside stderr. Log Analytics still receives every line, so the audit
    store and its retention are unchanged; the file is an ephemeral copy in the
    container's own writable layer that dies with the revision, and the dashboard is its
    only reader.

    Set by .github/workflows/deploy.yml from the repository variable DASHBOARD_ENABLED,
    not from a local apply. Like the two ingress variables above, a value set by hand is
    resolved back to the default by the next plan from main and silently revoked.
  EOT
  type        = bool
  default     = false
}
```

- [ ] **Step 2: Wire the two environment variables**

In `deploy/app.tf`, replace lines 189 to 194:

```hcl
      # Empty: stderr is the only sink, and Container Apps forwards it to the workspace
      # whose retention is the retention policy.
      env {
        name  = "CONTEXT_USAGE_LOG"
        value = ""
      }
```

with:

```hcl
      # Empty: stderr is the only sink, and Container Apps forwards it to the workspace
      # whose retention is the retention policy. The dashboard is the one exception, and
      # it is not a second store in the sense that matters: Log Analytics still receives
      # every line, and this file lives in the container's own writable layer, dies with
      # the revision, and is read by nothing but /dashboard.
      env {
        name  = "CONTEXT_USAGE_LOG"
        value = var.dashboard_enabled ? "/app/logs/usage.jsonl" : ""
      }

      # /app is chown'd to the app user in the image, and the read-only corpus mount is
      # at /app/context, so /app/logs is writable by uid 10001. server/usage.py creates
      # it on first write.
      dynamic "env" {
        for_each = var.dashboard_enabled ? [1] : []
        content {
          name  = "KI_ICL_DASHBOARD"
          value = "1"
        }
      }
```

- [ ] **Step 3: Add the three preconditions**

In `deploy/app.tf`, inside the existing `lifecycle` block, after the last existing `precondition`:

```hcl
    precondition {
      condition     = !(var.dashboard_enabled && !var.external_ingress_enabled)
      error_message = "dashboard_enabled without external_ingress_enabled mounts a dashboard nobody can reach: the app's own ingress stays internal-only, and no VPN gateway, ExpressRoute or peering exists anywhere in this configuration."
    }

    precondition {
      condition     = !(var.dashboard_enabled && var.max_replicas != 1)
      error_message = "dashboard_enabled needs max_replicas = 1. Each replica writes its own usage file, so above one the page shows whichever replica the load balancer happened to pick, and nothing on the page reveals that the numbers are partial. The Deploy workflow passes this alongside the switch; a local apply has to set it."
    }

    precondition {
      condition     = !(var.dashboard_enabled && var.auth_mode == "entra")
      error_message = "dashboard_enabled and auth_mode = \"entra\" are mutually exclusive. The dashboard has no authentication of its own and lists every artifact id regardless of grants, so an IP allow-list stops being a defensible gate the moment there are real identities to gate. The server refuses this combination at startup too."
    }
```

- [ ] **Step 4: Add the output**

In `deploy/outputs.tf`, after the `mcp_url` output:

```hcl
output "dashboard_url" {
  description = "The usage dashboard, when dashboard_enabled = true. Same ingress and allow-list as mcp_url, and no authentication of its own: it lists the whole corpus index regardless of grants, and /dashboard/purge rewrites the usage log."
  value       = var.dashboard_enabled ? "https://${azurerm_container_app.this.ingress[0].fqdn}/dashboard" : "dashboard_enabled = false"
}
```

- [ ] **Step 5: Declare the switch from repository settings**

In `.github/workflows/deploy.yml`, in the `terraform plan` step's script, after the `external_ingress` block and before the `allowed_cidrs` line:

```bash
          dashboard=false
          # The dashboard reads a usage file each replica writes for itself, so above
          # one replica the page shows a partial picture. variables.tf makes that a hard
          # precondition, and this array is what satisfies it.
          replicas=()
          if [ "${{ vars.DASHBOARD_ENABLED }}" = "true" ]; then
            dashboard=true
            replicas=(-var "max_replicas=1")
          fi
```

and extend the `terraform plan` invocation:

```bash
          terraform plan -input=false -out=tfplan.binary \
            -var "image=${{ needs.build-and-push.outputs.image }}" \
            -var "runner_image=$runner_image" \
            -var "external_ingress_enabled=$external_ingress" \
            -var "allowed_client_cidrs=$allowed_cidrs" \
            -var "dashboard_enabled=$dashboard" \
            ${replicas[@]+"${replicas[@]}"}
```

The `${replicas[@]+"${replicas[@]}"}` form is deliberate: the step runs under `set -u`, and a bare `"${replicas[@]}"` on an empty array is an unbound-variable error on bash before 4.4.

Extend the comment block above the step, which already explains why `runner_image` and the two ingress variables are declared here, with one sentence:

```
      # dashboard_enabled joins them for the same reason, and carries max_replicas with
      # it because the dashboard's own precondition requires a single replica.
```

- [ ] **Step 6: Check the Terraform**

```bash
cd deploy && terraform fmt -check && terraform validate
```

Expected: `fmt -check` prints nothing and exits 0; `validate` prints "Success! The configuration is valid."

If `validate` complains about an uninitialised backend, run `terraform init -backend=false` first. Do not run `terraform plan` or `terraform apply` locally: this configuration's switches are declared from repository settings, and a local plan resolves them back to their defaults.

- [ ] **Step 7: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS. Nothing in this task touches Python, so a failure here means something from an earlier task regressed.

- [ ] **Step 8: Commit**

```bash
git add deploy/variables.tf deploy/app.tf deploy/outputs.tf .github/workflows/deploy.yml
git commit -m "Let Deploy mount the usage dashboard, off by default and off in entra mode"
```

---

### Task 6: Documentation

The docs are written last because only now can they be written truthfully.

**Files:**
- Modify: `README.md:179`, `:296`, `:298-303`, `:408`
- Modify: `deploy/README.md`, the "Reaching it" section at `:222` and the CI/CD section at `:275`

**Interfaces:**
- Consumes: everything from Tasks 1 to 5.
- Produces: nothing code depends on.

- [ ] **Step 1: Update the file tree**

`README.md:179`. Replace:

```
scripts/dashboard.py                the usage dashboard
```

with:

```
server/dashboard.py                 usage aggregation, and the /dashboard routes when they are switched on
scripts/dashboard.py                the same dashboard, on loopback, needing no server
```

- [ ] **Step 2: Correct the sinks sentence**

`README.md:296`. Replace:

```
Sinks are stderr plus `logs/usage.jsonl`. Set `CONTEXT_USAGE_LOG=""` to leave stderr as the only one, which is what production wants: stdout is already collected by Log Analytics and a file would be a second store to own.
```

with:

```
Sinks are stderr plus `logs/usage.jsonl`. Set `CONTEXT_USAGE_LOG=""` to leave stderr as the only one, which is what production wants: stdout is already collected by Log Analytics and a file would be a second store to own. The deployed dashboard is the one exception and sets a path again, because it reads the file rather than the workspace; that copy lives in the container's writable layer and dies with the revision, so Log Analytics stays the only store anybody owns.
```

- [ ] **Step 3: Rewrite the dashboard section's opening**

`README.md:298-303`. Replace:

```
### The dashboard

`make dashboard` serves [scripts/dashboard.py](scripts/dashboard.py) on `:8010`.
It reads the log file directly, so it needs no MCP server running, and it re-polls every three seconds so records appear while you test.

All aggregation is pure Python functions over a list of records, covered by tests; [server/dashboard.html](server/dashboard.html) only renders what it is handed.
```

with:

```
### The dashboard

Two hosts, one page, one set of functions.

`make dashboard` serves [scripts/dashboard.py](scripts/dashboard.py) on `:8010`.
It reads the log file directly, so it needs no MCP server running, and it re-polls every three seconds so records appear while you test.

`KI_ICL_DASHBOARD=1` mounts the same thing at `/dashboard` on the server's own port, behind whatever ingress the server is behind (`make serve-http-dashboard` locally, and `dashboard_enabled = true` in [deploy/](deploy/) for the Azure app).
Unset, not one of those routes is registered, which is the default everywhere.

All aggregation is pure Python functions over a list of records in [server/dashboard.py](server/dashboard.py), covered by tests; [server/dashboard.html](server/dashboard.html) only renders what it is handed, and addresses its endpoints relative to wherever it was served from so one file works under both hosts.

**The mounted dashboard is not access-controlled, and two things about it are worse than merely unauthenticated.**
Its catalog panel comes from `live_catalog`, which reads `_manifest.json` directly and lists every artifact id in the corpus regardless of who may read it, because it describes the corpus rather than answering a caller.
And `/dashboard/purge` rewrites the usage log, which is the only route in the system that edits the audit trail.
So the ingress in front of it is the entire gate.
That is a disclosed trade for a demo reached from one allow-listed address, on the same footing as `external_ingress_enabled` itself, and it is why the server refuses the switch outright in `entra` mode and Terraform refuses the same combination: an IP allow-list stops being a defensible gate the moment there are real identities to gate.
```

- [ ] **Step 4: Qualify the retention claim**

`README.md:408`. Replace:

```
- **Retention 90 days**, enforced where the store is: production sets `CONTEXT_USAGE_LOG=""` so Log Analytics is the only store, with workspace retention set there and the workspace pinned to an EU region. Token validation is local and the JWKS fetch carries only public signing keys, so there is no Art. 44 transfer in the auth path.
```

with:

```
- **Retention 90 days**, enforced where the store is: production sets `CONTEXT_USAGE_LOG=""` so Log Analytics is the only store, with workspace retention set there and the workspace pinned to an EU region. The deployed dashboard sets that variable to a path again, and does not change this: the file is in the container's writable layer, it dies with the revision, nothing reads it but `/dashboard`, and Log Analytics still receives every line. It is also mutually exclusive with `auth_mode = "entra"`, so it can never coexist with a log that records an actor. Token validation is local and the JWKS fetch carries only public signing keys, so there is no Art. 44 transfer in the auth path.
```

- [ ] **Step 5: Extend deploy/README.md's "Reaching it"**

After the `From your own machine, once reachable:` code block that ends on line 248, and before the `Once auth_mode = entra` paragraph, insert:

````
### The usage dashboard

Off by default. When it is on, it is at `/dashboard` on the same host, port, ingress and allow-list as `/mcp`:

```bash
gh variable set DASHBOARD_ENABLED --body true
gh workflow run deploy.yml --ref main
terraform output dashboard_url
```

Demo scaffolding, and treated as such by three preconditions that fail the plan rather than warn.
It needs `external_ingress_enabled` (otherwise nobody can reach it), it needs `max_replicas = 1` (each replica writes its own usage file, so above one the page shows a partial picture with nothing on it saying so, and the Deploy workflow passes the value alongside the switch), and it is refused outright with `auth_mode = "entra"`.

That last one is the important one.
The dashboard has no authentication of its own, its catalog panel lists every artifact id in the corpus regardless of grants, and `/dashboard/purge` rewrites the usage log, so the IP allow-list is the entire gate.
That is defensible for a demo reached from one address with `auth_mode` still `off` and no actor recorded anywhere; it stops being defensible the moment real identities exist, which is what the precondition encodes.
[server/mcp_server.py](../server/mcp_server.py) refuses the same pair at startup, so an `az containerapp update` that sets the environment variable by hand produces a revision that will not start rather than one that quietly serves it.

Switching it on also sets `CONTEXT_USAGE_LOG` to a path, which turns the file sink back on alongside stderr.
Log Analytics still receives every line, and the file is an ephemeral copy in the container's own writable layer that dies with the revision, so the audit store and its retention are unchanged.
The practical consequence is that a redeploy resets what the dashboard shows.
````

- [ ] **Step 6: Add it to the CI/CD list of repository-declared switches**

`deploy/README.md:276`. Replace:

```
`deploy/ci.auto.tfvars` carries the baseline every apply needs (`create_role_assignments = false`, `acr_pull_confirmed = true`), and the two ingress variables come from this repository's Actions settings instead: the variable `EXTERNAL_INGRESS_ENABLED`, and the secret `ALLOWED_CLIENT_CIDRS` holding the allow-list as JSON.
```

with:

```
`deploy/ci.auto.tfvars` carries the baseline every apply needs (`create_role_assignments = false`, `acr_pull_confirmed = true`), and the switches come from this repository's Actions settings instead: the variables `EXTERNAL_INGRESS_ENABLED` and `DASHBOARD_ENABLED`, and the secret `ALLOWED_CLIENT_CIDRS` holding the allow-list as JSON.
```

and extend the code block on lines 278 to 281 with a third line:

```bash
gh variable set DASHBOARD_ENABLED --body true
```

- [ ] **Step 7: Check the links and the prose**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS. Some repositories test their own README links; if a link test exists it will catch a typo in the new `server/dashboard.py` reference.

Read the two changed sections back in a Markdown preview and confirm the nested code fence inside step 5's inserted block renders correctly. The insert contains a fenced `bash` block, so it must be pasted as literal Markdown into `deploy/README.md`, not nested inside another fence.

Confirm no em dash characters were introduced. Only added lines are checked, because both files already contain some and rewriting prose this task did not touch is out of scope. The character is written as an escape rather than literally, so the check does not match itself:

```bash
git diff -U0 -- README.md deploy/README.md | .venv/bin/python -c "
import sys
added = [
    line for line in sys.stdin
    if line.startswith('+') and not line.startswith('+++') and '\u2014' in line
]
print(''.join(added) if added else 'none')
"
```

Expected: `none`.

- [ ] **Step 8: Commit**

```bash
git add README.md deploy/README.md
git commit -m "Document the dashboard's two hosts, and what the mounted one exposes"
```

---

## Final verification

- [ ] **Full suite green:** `.venv/bin/python -m pytest -q`
- [ ] **Terraform clean:** `cd deploy && terraform fmt -check && terraform validate`
- [ ] **Default posture unchanged:** `.venv/bin/python -m pytest tests/test_dashboard_routes.py::test_no_dashboard_routes_unless_asked -v`
- [ ] **Both hosts render the same numbers from the same file:** `make dashboard` on `:8010` and `make serve-http-dashboard` on `:8000/dashboard`, side by side, after running `scripts/demo.py`.
- [ ] **The entra guard bites:** `KI_ICL_AUTH=entra KI_ICL_DASHBOARD=1 .venv/bin/python server/mcp_server.py --http` exits rather than starting.

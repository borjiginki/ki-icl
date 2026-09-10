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
    # Every other answer from either host says this, and a POST response that a shared
    # cache stored would show a mark that is no longer there.
    assert response.headers["cache-control"] == "no-store"
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
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"key": "method/nope", "removed": 1}
    remaining = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert [r["event"] for r in remaining] == ["purge"]


def test_purge_refuses_a_missing_key(log):
    with TestClient(mcp_server.build_server(dashboard=True).http_app()) as client:
        response = client.post("/dashboard/purge", json={})

    assert response.status_code == 400


def test_the_data_route_lists_artifact_ids_the_caller_could_never_read(
    log, tmp_path, monkeypatch
):
    """The exposure the whole guard set exists to contain, pinned so it cannot be lost.

    `/dashboard/data` carries `live_catalog`, which reads the manifests directly and
    names every artifact in the corpus with no principal anywhere in the call, while
    the same ids are refused to the same caller through /mcp. Four Terraform
    preconditions, the entra refusal in `refuse_unsafe_start` and the wide-bind warning
    all exist for that one payload, and until now nothing failed if a future change
    quietly dropped it. That would leave every one of those guards reading as
    superstition to whoever came next, and the likely response is to delete them.

    So this test is here to be read as much as to be run: if it starts failing because
    the catalog no longer leaks, the guards can go, and that is a decision to make
    deliberately rather than by inference.
    """
    import yaml

    from server import access, artifacts
    from tests.conftest import VALID_POLICY, write_artifact, write_policy

    corpus = tmp_path / "corpus"
    team = corpus / "domains" / "team"
    team.mkdir(parents=True)
    write_artifact(team, "works-council-notes", README__md="# Confidential\n")
    (team / "_manifest.json").write_text(
        json.dumps(
            {
                "domain": "team",
                "artifacts": [{"id": "works-council-notes", "version_id": "cccc3333"}],
            }
        ),
        encoding="utf-8",
    )
    write_policy(corpus)
    monkeypatch.setattr(artifacts, "ARTIFACTS_ROOT", corpus)

    with TestClient(mcp_server.build_server(dashboard=True).http_app()) as client:
        payload = client.get("/dashboard/data").json()

    # The policy grants team to ctx.people alone, and nothing about reaching an
    # allow-listed IP confers a role, so the dashboard's viewer is this caller.
    refused = artifacts.get_artifact_payload(
        "team",
        ["works-council-notes"],
        principal=access.ANONYMOUS,
        policy=access.parse_policy(yaml.safe_load(VALID_POLICY), mode=access.Mode.ENFORCE),
    )

    assert refused["status"] == "forbidden"
    assert "team/works-council-notes" in payload["catalog"]["artifacts"]

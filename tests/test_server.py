"""Wiring: the three tools are registered under their exact names and return JSON."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def _call(mcp, name: str, args: dict) -> dict:
    return json.loads((await mcp.call_tool(name, args)).content[0].text)


async def test_all_three_tools_are_registered_under_their_exact_names():
    from server.mcp_server import mcp

    names = {t.name for t in await mcp.list_tools()}

    assert {"list_domains", "get_domain_manifest", "get_artifact", "report_gap"} <= names


async def test_every_tool_returns_the_payload_the_read_path_built(monkeypatch, catalog: Path):
    from server import artifacts
    from server.mcp_server import mcp

    monkeypatch.setattr(artifacts, "ARTIFACTS_ROOT", catalog)

    domains = await _call(mcp, "list_domains", {})
    assert [d["id"] for d in domains["domains"]] == ["company"]

    manifest = await _call(mcp, "get_domain_manifest", {"domain": "company"})
    assert len(manifest["artifacts"]) == 2

    fetched = await _call(mcp, "get_artifact", {"domain": "company", "ids": "expense-policy"})
    assert fetched["artifacts"][0]["status"] == "found"


async def test_get_artifact_accepts_a_list_of_ids_over_the_wire(monkeypatch, catalog: Path):
    """Agents pass both a bare string and a list; the schema must permit both."""
    from server import artifacts
    from server.mcp_server import mcp

    monkeypatch.setattr(artifacts, "ARTIFACTS_ROOT", catalog)

    fetched = await _call(
        mcp, "get_artifact", {"domain": "company", "ids": ["expense-policy", "nope"]}
    )

    assert [a["status"] for a in fetched["artifacts"]] == ["found", "not_found"]


async def test_the_instructions_tell_an_agent_to_report_a_gap():
    """Without this the tool exists and is never called: an agent that finds nothing
    has no reason to think anyone wants to know."""
    from server.mcp_server import mcp

    assert "report_gap" in (mcp.instructions or "")


async def test_report_gap_forbids_passing_the_users_words():
    """The docstring is the tool description the model sees, and the privacy rule has
    to reach the model, not just the validator."""
    from server.mcp_server import mcp

    doc = ((await mcp.get_tool("report_gap")).description or "").lower()

    assert "kebab-case" in doc
    assert "never pass their words" in doc


async def test_get_artifact_tells_the_model_not_to_substitute_a_similar_id():
    """The docstring is the tool description the model sees. This invariant has to
    reach the model, not just the payload."""
    from server.mcp_server import mcp

    doc = (await mcp.get_tool("get_artifact")).description or ""

    assert "not_found" in doc
    assert "substitute" in doc.lower()


async def test_the_script_runs_as_a_subprocess_the_way_a_stdio_client_launches_it(
    tmp_path: Path, catalog: Path
):
    """Running `python server/mcp_server.py` puts server/ on sys.path, not the repo
    root, so `from server import artifacts` fails unless the script bootstraps it."""
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport

    repo = Path(__file__).resolve().parent.parent
    usage_log = tmp_path / "usage.jsonl"
    transport = StdioTransport(
        command=sys.executable,
        args=[str(repo / "server" / "mcp_server.py")],
        env={
            "CONTEXT_ROOT": str(catalog),
            # A child process is out of monkeypatch's reach, so the sink has to be
            # redirected the way an operator would: by environment.
            "CONTEXT_USAGE_LOG": str(usage_log),
            "PATH": "/usr/bin:/bin",
        },
    )

    async with Client(transport) as client:
        names = {t.name for t in await client.list_tools()}
        result = await client.call_tool("list_domains", {})

    assert {"list_domains", "get_domain_manifest", "get_artifact", "report_gap"} <= names
    assert json.loads(result.content[0].text)["domains"][0]["id"] == "company"
    # First line is the startup catalog snapshot; the call follows it.
    lines = [json.loads(line) for line in usage_log.read_text().strip().splitlines()]
    assert lines[0]["event"] == "catalog"
    assert lines[-1]["tool"] == "list_domains"


# --- the factory ------------------------------------------------------------


async def test_build_server_produces_an_independently_configurable_server():
    """`auth=` is constructor-only in FastMCP, so without a factory there is no way to
    exercise more than one auth configuration in a single process, and the HTTP tests
    could not exist at all."""
    from server.mcp_server import build_server

    unauthenticated = build_server()
    guarded = build_server(auth=_static_auth())

    assert unauthenticated.auth is None
    assert guarded.auth is not None
    assert {t.name for t in await unauthenticated.list_tools()} == {
        t.name for t in await guarded.list_tools()
    }


def _static_auth():
    from fastmcp.server.auth.providers.jwt import StaticTokenVerifier

    return StaticTokenVerifier(tokens={"demo-token-x": {"client_id": "c", "scopes": []}})


def test_the_module_level_server_is_still_the_one_the_tests_and_dashboard_import():
    """The refactor must not move the name: tests/test_gaps.py and scripts/dashboard.py
    both import `server.mcp_server.mcp`."""
    from server import mcp_server

    assert mcp_server.mcp.name == "ki-icl"


def test_error_details_are_masked_so_an_exception_cannot_carry_content_to_the_client():
    """FastMCP returns exception text verbatim by default, which makes the error path a
    leak channel: a traceback naming an artifact id would disclose exactly what
    authorization exists to withhold."""
    from server import mcp_server

    assert mcp_server.mcp._mask_error_details is True

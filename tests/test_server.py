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

    assert {"list_domains", "get_domain_manifest", "get_artifact"} <= names


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

    assert {"list_domains", "get_domain_manifest", "get_artifact"} <= names
    assert json.loads(result.content[0].text)["domains"][0]["id"] == "company"
    assert json.loads(usage_log.read_text())["tool"] == "list_domains"

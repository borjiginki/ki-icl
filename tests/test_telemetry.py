"""The six telemetry additions the dashboard needs.

Each test here exists because a panel cannot be built without the field it pins.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.usage import catalog_record, records_from_call  # noqa: E402


# --- 1.4 bytes and skipped --------------------------------------------------


def test_a_hit_records_the_bytes_it_returned_not_only_the_file_count():
    """File count says nothing about cost. Bytes is what fills a context window."""
    payload = {
        "domain": "hr",
        "artifacts": [
            {
                "status": "found",
                "id": "expense-policy",
                "version_id": "5dff64bd",
                "file_count": 2,
                "files": [
                    {"path": "README.md", "size": 2033},
                    {"path": "artifact.yaml", "size": 237},
                ],
                "skipped_files": [],
            }
        ],
    }

    (record,) = records_from_call("get_artifact", {"domain": "hr"}, payload)

    assert record["bytes"] == 2270
    assert record["skipped"] == 0


def test_files_dropped_by_the_size_cap_are_counted():
    payload = {
        "domain": "hr",
        "artifacts": [
            {
                "status": "found",
                "id": "big",
                "version_id": "v",
                "file_count": 1,
                "files": [{"path": "README.md", "size": 10}],
                "skipped_files": ["huge.md", "also-huge.csv"],
            }
        ],
    }

    (record,) = records_from_call("get_artifact", {"domain": "hr"}, payload)

    assert record["skipped"] == 2
    assert record["bytes"] == 10


def test_a_miss_records_no_bytes():
    payload = {"domain": "hr", "artifacts": [{"status": "not_found", "id": "nope"}]}

    (record,) = records_from_call("get_artifact", {"domain": "hr"}, payload)

    assert "bytes" not in record


# --- 1.5 which artifacts were offered ---------------------------------------


def test_a_manifest_read_records_which_ids_it_offered():
    """Chosen versus offered is the only feedback loop on description quality."""
    payload = {
        "domain": "hr",
        "artifacts": [{"id": "discovery-workshop"}, {"id": "expense-policy"}],
    }

    (record,) = records_from_call("get_domain_manifest", {"domain": "hr"}, payload)

    assert record["offered"] == ["discovery-workshop", "expense-policy"]
    assert record["artifact_count"] == 2


# --- 1.6 the catalog snapshot -----------------------------------------------


def test_a_catalog_record_describes_what_exists_at_startup(catalog: Path):
    """Without it, a miss for something deleted looks like a miss for something
    that never existed."""
    record = catalog_record(catalog)

    assert record["event"] == "catalog"
    assert record["domain_count"] == 1
    assert record["artifact_count"] == 2
    assert record["bytes"] > 0
    assert record["artifacts"] == {
        "company/discovery-workshop": "bbbb2222",
        "company/expense-policy": "aaaa1111",
    }


def test_a_catalog_record_on_an_empty_root_is_still_valid(tmp_path: Path):
    record = catalog_record(tmp_path / "nothing")

    assert record["domain_count"] == 0
    assert record["artifacts"] == {}


# --- 1.1 / 1.2 / 1.3, end to end through the middleware ---------------------


@pytest.fixture
def logged(tmp_path: Path, monkeypatch, catalog: Path):
    """Calls through the real server; returns a reader for what was logged."""
    from server import artifacts, usage

    path = tmp_path / "usage.jsonl"
    monkeypatch.setattr(artifacts, "ARTIFACTS_ROOT", catalog)
    monkeypatch.setattr(usage, "USAGE_LOG", usage.UsageLog(path=path, stream=None))

    def read() -> list[dict]:
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text().strip().splitlines()]

    return read


async def test_records_from_one_connection_share_a_session_and_increment_seq(logged):
    """The funnel needs to know these calls came from one agent, in this order."""
    from fastmcp import Client

    from server.mcp_server import mcp

    async with Client(mcp) as client:
        await client.call_tool("list_domains", {})
        await client.call_tool("get_domain_manifest", {"domain": "company"})

    records = logged()
    assert len({r["session"] for r in records}) == 1
    assert [r["tool"] for r in records] == ["list_domains", "get_domain_manifest"]
    assert records[0]["seq"] < records[1]["seq"]


async def test_two_connections_get_different_sessions(logged):
    from fastmcp import Client

    from server.mcp_server import mcp

    for _ in range(2):
        async with Client(mcp) as client:
            await client.call_tool("list_domains", {})

    assert len({r["session"] for r in logged()}) == 2


async def test_timestamps_carry_milliseconds_so_a_batch_can_be_ordered(logged):
    """Second resolution collapsed a batch call's records onto one instant."""
    from fastmcp import Client

    from server.mcp_server import mcp

    async with Client(mcp) as client:
        await client.call_tool(
            "get_artifact",
            {"domain": "company", "ids": ["expense-policy", "discovery-workshop"]},
        )

    stamps = [r["ts"] for r in logged()]
    assert all(s.endswith("Z") and "." in s for s in stamps), stamps


async def test_a_call_outside_a_request_context_is_still_logged(logged):
    """`Context.session_id` RAISES when there is no session, rather than returning
    None. A getattr default does not catch that, and one raising property silently
    killed logging for the entire call."""
    from server.mcp_server import mcp

    await mcp.call_tool("get_artifact", {"domain": "company", "ids": ["expense-policy"]})

    (record,) = logged()
    assert record["outcome"] == "found"
    assert "session" not in record  # honestly absent, not faked
    assert record["ts"].endswith("Z")


async def test_a_tool_that_raises_is_recorded_and_the_error_still_propagates(logged, monkeypatch):
    """A failing tool is the one thing a usage log must never hide."""
    from fastmcp import Client

    from server import artifacts
    from server.mcp_server import mcp

    def boom(*_args, **_kwargs):
        raise RuntimeError("blob unreachable")

    monkeypatch.setattr(artifacts, "list_domains_payload", boom)

    with pytest.raises(Exception):
        async with Client(mcp) as client:
            await client.call_tool("list_domains", {})

    (record,) = logged()
    assert record["outcome"] == "error"
    assert record["tool"] == "list_domains"
    # FastMCP wraps a tool's exception in ToolError, so the outer type name is that.
    # What has to survive is the message, which is the diagnosable part.
    assert "blob unreachable" in record["error"]

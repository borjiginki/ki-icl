"""Usage logging: one record per artifact actually looked up, hit or miss.

The miss records are the point. A run of `not_found` on the same id is the signal
that says what to write next, and it is the only place that signal exists.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.usage import UsageLog, records_from_call  # noqa: E402


# --- what a call turns into ------------------------------------------------


def test_a_hit_records_the_id_the_outcome_and_the_version_served():
    payload = {
        "domain": "company",
        "artifacts": [
            {
                "status": "found",
                "id": "expense-policy",
                "version_id": "b0f9dd0a",
                "file_count": 2,
                "files": [],
            }
        ],
    }

    (record,) = records_from_call("get_artifact", {"domain": "company"}, payload)

    assert record == {
        "event": "context_use",
        "tool": "get_artifact",
        "domain": "company",
        "id": "expense-policy",
        "outcome": "found",
        "version_id": "b0f9dd0a",
        "file_count": 2,
        "bytes": 0,
        "skipped": 0,
    }


def test_a_batch_records_each_id_separately():
    payload = {
        "domain": "company",
        "artifacts": [
            {"status": "found", "id": "expense-policy", "version_id": "b0f9", "file_count": 2},
            {"status": "not_found", "id": "travel-policy"},
        ],
    }

    records = records_from_call("get_artifact", {"domain": "company"}, payload)

    assert [(r["id"], r["outcome"]) for r in records] == [
        ("expense-policy", "found"),
        ("travel-policy", "not_found"),
    ]


def test_a_miss_records_no_version_and_no_file_count():
    payload = {"domain": "company", "artifacts": [{"status": "not_found", "id": "nope"}]}

    (record,) = records_from_call("get_artifact", {"domain": "company"}, payload)

    assert "version_id" not in record
    assert "file_count" not in record


def test_an_unknown_domain_records_the_domain_and_no_ids():
    payload = {"status": "not_found", "domain": "compnay", "known": ["company"]}

    (record,) = records_from_call("get_artifact", {"domain": "compnay"}, payload)

    assert record["outcome"] == "not_found"
    assert record["domain"] == "compnay"
    assert "id" not in record


def test_a_manifest_read_records_the_domain_and_how_much_it_offered():
    payload = {"domain": "company", "artifacts": [{"id": "a"}, {"id": "b"}]}

    (record,) = records_from_call("get_domain_manifest", {"domain": "company"}, payload)

    assert record == {
        "event": "context_use",
        "tool": "get_domain_manifest",
        "domain": "company",
        "outcome": "found",
        "artifact_count": 2,
        "offered": ["a", "b"],
    }


def test_a_discovery_call_is_recorded_too():
    payload = {"domains": [{"id": "company"}]}

    (record,) = records_from_call("list_domains", {}, payload)

    assert record["tool"] == "list_domains"
    assert record["domain_count"] == 1


def test_no_record_ever_carries_a_caller_identity():
    """The POC records what was looked up, never who looked it up, so no personal
    data is processed and no Art. 6 basis is needed."""
    payload = {
        "domain": "company",
        "artifacts": [{"status": "found", "id": "x", "version_id": "v", "file_count": 1}],
    }

    for tool, p in [
        ("get_artifact", payload),
        ("get_domain_manifest", {"domain": "company", "artifacts": []}),
        ("list_domains", {"domains": []}),
    ]:
        for record in records_from_call(tool, {"domain": "company"}, p):
            assert not {"actor", "user", "email", "oid", "sub"} & set(record)


def test_a_tool_that_is_not_a_context_tool_records_nothing():
    assert records_from_call("some_other_tool", {}, {"anything": True}) == []


# --- the sink ---------------------------------------------------------------


def test_records_are_appended_to_the_file_as_one_json_object_per_line(tmp_path: Path):
    path = tmp_path / "logs" / "usage.jsonl"
    log = UsageLog(path=path, stream=None)

    log.write({"event": "context_use", "id": "a"})
    log.write({"event": "context_use", "id": "b"})

    lines = path.read_text().strip().splitlines()
    assert [json.loads(line)["id"] for line in lines] == ["a", "b"]


def test_every_record_is_stamped_with_a_utc_timestamp(tmp_path: Path):
    path = tmp_path / "usage.jsonl"
    UsageLog(path=path, stream=None).write({"event": "context_use"})

    assert json.loads(path.read_text())["ts"].endswith("Z")


def test_the_file_sink_is_off_when_no_path_is_configured(tmp_path: Path, capsys):
    UsageLog(path=None, stream=sys.stderr).write({"event": "context_use", "id": "a"})

    assert list(tmp_path.iterdir()) == []
    assert "context_use" in capsys.readouterr().err


def test_a_broken_sink_never_raises_into_a_call(tmp_path: Path):
    """A logging bug must stay an annoyance, never an outage."""
    log = UsageLog(path=tmp_path / "usage.jsonl", stream=None)
    log.write({"event": "context_use", "unserialisable": object()})  # must not raise


# --- end to end through the server -----------------------------------------


@pytest.fixture
def usage_file(tmp_path: Path, monkeypatch, catalog: Path):
    from server import artifacts, usage

    path = tmp_path / "usage.jsonl"
    monkeypatch.setattr(artifacts, "ARTIFACTS_ROOT", catalog)
    monkeypatch.setattr(usage, "USAGE_LOG", UsageLog(path=path, stream=None))
    return path


async def test_a_real_call_through_the_server_lands_in_the_log(usage_file: Path):
    from server.mcp_server import mcp

    await mcp.call_tool(
        "get_artifact", {"domain": "company", "ids": ["expense-policy", "travel-policy"]}
    )

    records = [json.loads(line) for line in usage_file.read_text().strip().splitlines()]
    assert [(r["id"], r["outcome"]) for r in records] == [
        ("expense-policy", "found"),
        ("travel-policy", "not_found"),
    ]
    assert all(r["duration_ms"] >= 0 for r in records)


async def test_logging_is_wired_by_middleware_so_no_tool_knows_about_it(usage_file: Path):
    """Every context tool logs without a line of logging code in the tool itself."""
    from server.mcp_server import mcp

    await mcp.call_tool("list_domains", {})
    await mcp.call_tool("get_domain_manifest", {"domain": "company"})

    tools = [json.loads(line)["tool"] for line in usage_file.read_text().strip().splitlines()]
    assert tools == ["list_domains", "get_domain_manifest"]

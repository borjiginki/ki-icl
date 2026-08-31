"""Reported gaps: an agent saying "the manifest had no answer for X".

This is the signal the miss records could never carry. A well-behaved agent reads
the manifest, finds nothing, and stops without calling get_artifact, so nothing was
recorded. Now it can say so.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.usage import gap_record, normalise_topic  # noqa: E402


# --- the topic is a label, never a sentence ---------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("parental-leave", "parental-leave"),
        ("Parental Leave", "parental-leave"),
        ("  onboarding  ", "onboarding"),
        ("new_starter_setup", "new-starter-setup"),
        ("Travel Policy 2026", "travel-policy-2026"),
    ],
)
def test_a_reasonable_topic_is_normalised_rather_than_rejected(raw, expected):
    """Forgiving about shape, strict about content: an agent should not have to
    guess the exact casing to be heard."""
    assert normalise_topic(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "!!!",
        "what is our parental leave entitlement for twins born in march",  # a sentence
        "a" * 60,  # too long
    ],
)
def test_free_text_and_overlong_topics_are_rejected(raw):
    """The cap is what stops this becoming a store of user questions, which would
    carry NDA-covered material and personal data with no Art. 6 basis."""
    assert normalise_topic(raw) is None


def test_a_gap_record_carries_the_domain_the_topic_and_nothing_else_identifying():
    record = gap_record("hr", "parental-leave")

    assert record["event"] == "context_gap"
    assert record["domain"] == "hr"
    assert record["topic"] == "parental-leave"
    assert not {"question", "user", "actor", "email"} & set(record)


def test_an_unusable_topic_produces_no_record():
    assert gap_record("hr", "what is our parental leave policy exactly") is None


# --- end to end through the tool --------------------------------------------


@pytest.fixture
def logged(tmp_path: Path, monkeypatch, catalog: Path):
    from server import artifacts, usage

    path = tmp_path / "usage.jsonl"
    monkeypatch.setattr(artifacts, "ARTIFACTS_ROOT", catalog)
    monkeypatch.setattr(usage, "USAGE_LOG", usage.UsageLog(path=path, stream=None))
    return lambda: [
        json.loads(line) for line in path.read_text().strip().splitlines()
    ] if path.is_file() else []


async def test_the_tool_records_the_gap_and_tells_the_agent_it_landed(logged):
    from fastmcp import Client

    from server.mcp_server import mcp

    async with Client(mcp) as client:
        result = await client.call_tool(
            "report_gap", {"domain": "company", "topic": "Parental Leave"}
        )

    assert json.loads(result.content[0].text)["status"] == "recorded"
    (record,) = [r for r in logged() if r.get("event") == "context_gap"]
    assert record["topic"] == "parental-leave"
    assert record["session"]


async def test_the_tool_refuses_a_sentence_and_says_why(logged):
    from fastmcp import Client

    from server.mcp_server import mcp

    async with Client(mcp) as client:
        result = await client.call_tool(
            "report_gap",
            {"domain": "company", "topic": "the user wanted to know about leave for twins"},
        )

    payload = json.loads(result.content[0].text)
    assert payload["status"] == "rejected"
    assert "topic" in payload["reason"].lower()
    assert not [r for r in logged() if r.get("event") == "context_gap"]


async def test_a_gap_against_an_unknown_domain_is_still_recorded(logged):
    """Not knowing which domain it belongs in is itself worth knowing."""
    from fastmcp import Client

    from server.mcp_server import mcp

    async with Client(mcp) as client:
        await client.call_tool("report_gap", {"domain": "finance", "topic": "invoicing"})

    (record,) = [r for r in logged() if r.get("event") == "context_gap"]
    assert record["domain"] == "finance"

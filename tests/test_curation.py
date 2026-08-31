"""Gaps on the dashboard, and dismissing the ones that are not worth writing.

Suggestions arrive with no filtering beyond the agent's judgement, so the panel is
only useful if clearing it is one click. Dismissals are curation decisions, not
telemetry, so they live in their own file rather than in the append-only log.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.dashboard import aggregate, dismiss, read_curation, restore  # noqa: E402


def rec(**kw):
    return {"ts": "2026-08-31T08:00:00.000Z", **kw}


@pytest.fixture
def records() -> list[dict]:
    return [
        {"ts": "2026-08-31T07:59:00.000Z", "event": "catalog", "artifacts": {"hr/expense-policy": "v1"}},
        # Two agents independently reported the same gap.
        rec(event="context_gap", session="aaa", domain="hr", topic="parental-leave"),
        rec(event="context_gap", session="bbb", domain="hr", topic="parental-leave"),
        rec(event="context_gap", session="ccc", domain="hr", topic="office-plants"),
        # And one agent guessed an id instead of reporting.
        rec(event="context_use", session="ddd", tool="get_artifact", domain="hr",
            id="onboarding", outcome="not_found"),
    ]


def test_a_reported_gap_appears_in_what_to_write_next(records):
    rows = {r["key"]: r for r in aggregate(records)["misses"]}

    assert rows["hr/parental-leave"]["count"] == 2
    assert rows["hr/parental-leave"]["sessions"] == 2


def test_a_reported_gap_and_a_guessed_id_are_both_shown(records):
    keys = {r["key"] for r in aggregate(records)["misses"]}

    assert keys == {"hr/parental-leave", "hr/office-plants", "hr/onboarding"}


def test_each_row_says_how_the_signal_arrived(records):
    """A gap an agent reported deliberately is stronger evidence than an id it
    guessed at, and the two should not be indistinguishable."""
    rows = {r["key"]: r for r in aggregate(records)["misses"]}

    assert rows["hr/parental-leave"]["sources"] == ["reported"]
    assert rows["hr/onboarding"]["sources"] == ["guessed"]


def test_the_same_topic_reported_and_guessed_merges_into_one_row(records):
    records.append(rec(event="context_use", session="eee", tool="get_artifact",
                       domain="hr", id="parental-leave", outcome="not_found"))

    rows = {r["key"]: r for r in aggregate(records)["misses"]}

    assert rows["hr/parental-leave"]["count"] == 3
    assert sorted(rows["hr/parental-leave"]["sources"]) == ["guessed", "reported"]


def test_gaps_rank_above_less_wanted_ones(records):
    assert aggregate(records)["misses"][0]["key"] == "hr/parental-leave"


# --- dismissal --------------------------------------------------------------


def test_a_dismissed_row_disappears_from_the_panel(records, tmp_path: Path):
    curation = tmp_path / "curation.json"
    dismiss(curation, "hr/office-plants")

    keys = {r["key"] for r in aggregate(records, curation=read_curation(curation))["misses"]}

    assert "hr/office-plants" not in keys
    assert "hr/parental-leave" in keys


def test_a_dismissed_row_is_still_listed_separately_with_its_current_count(records, tmp_path: Path):
    """Dismiss something that keeps getting asked for and you should be able to see
    you got it wrong."""
    curation = tmp_path / "curation.json"
    dismiss(curation, "hr/parental-leave")

    result = aggregate(records, curation=read_curation(curation))

    (row,) = result["dismissed"]
    assert row["key"] == "hr/parental-leave"
    assert row["count"] == 2


def test_restoring_puts_a_row_back(records, tmp_path: Path):
    curation = tmp_path / "curation.json"
    dismiss(curation, "hr/office-plants")
    restore(curation, "hr/office-plants")

    keys = {r["key"] for r in aggregate(records, curation=read_curation(curation))["misses"]}

    assert "hr/office-plants" in keys


def test_dismissing_twice_is_harmless(tmp_path: Path):
    curation = tmp_path / "curation.json"
    dismiss(curation, "hr/x")
    dismiss(curation, "hr/x")

    assert read_curation(curation)["dismissed"] == ["hr/x"]


def test_restoring_something_never_dismissed_is_harmless(tmp_path: Path):
    curation = tmp_path / "curation.json"
    restore(curation, "hr/never")

    assert read_curation(curation)["dismissed"] == []


def test_a_missing_or_corrupt_curation_file_reads_as_nothing_dismissed(tmp_path: Path):
    assert read_curation(tmp_path / "nope.json")["dismissed"] == []

    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert read_curation(broken)["dismissed"] == []


def test_dismissal_does_not_touch_the_usage_log(records, tmp_path: Path):
    """Curation is a decision, not an observation. The log stays append-only telemetry."""
    curation = tmp_path / "curation.json"
    before = json.dumps(records)

    dismiss(curation, "hr/office-plants")

    assert json.dumps(records) == before

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
    dismiss(curation, "hr/office-plants", count=1)

    keys = {r["key"] for r in aggregate(records, curation=read_curation(curation))["misses"]}

    assert "hr/office-plants" not in keys
    assert "hr/parental-leave" in keys


def test_a_dismissed_row_is_still_listed_separately_with_its_current_count(records, tmp_path: Path):
    """Dismiss something that keeps getting asked for and you should be able to see
    you got it wrong."""
    curation = tmp_path / "curation.json"
    dismiss(curation, "hr/parental-leave", count=2)

    result = aggregate(records, curation=read_curation(curation))

    (row,) = result["dismissed"]
    assert row["key"] == "hr/parental-leave"
    assert row["count"] == 2


def test_restoring_puts_a_row_back(records, tmp_path: Path):
    curation = tmp_path / "curation.json"
    dismiss(curation, "hr/office-plants", count=1)
    restore(curation, "hr/office-plants")

    keys = {r["key"] for r in aggregate(records, curation=read_curation(curation))["misses"]}

    assert "hr/office-plants" in keys


def test_dismissing_twice_is_harmless(tmp_path: Path):
    curation = tmp_path / "curation.json"
    dismiss(curation, "hr/x", count=1)
    dismiss(curation, "hr/x", count=1)

    assert [d["key"] for d in read_curation(curation)["dismissed"]] == ["hr/x"]


# --- a dismissal is a judgement on the evidence so far, not a permanent mute ---


def test_new_demand_after_a_dismissal_brings_the_row_back(records, tmp_path: Path):
    """Dismissing means "not worth writing given what I have seen". Somebody asking
    again is new information, and burying it makes the panel lie by omission."""
    curation = tmp_path / "curation.json"
    dismiss(curation, "hr/parental-leave", count=2)   # the count when it was dismissed

    records.append(rec(event="context_gap", session="zzz",
                       domain="hr", topic="parental-leave"))
    result = aggregate(records, curation=read_curation(curation))

    keys = {r["key"] for r in result["misses"]}
    assert "hr/parental-leave" in keys
    assert result["dismissed"] == []


def test_a_row_that_came_back_says_so(records, tmp_path: Path):
    curation = tmp_path / "curation.json"
    dismiss(curation, "hr/parental-leave", count=2)
    records.append(rec(event="context_gap", session="zzz",
                       domain="hr", topic="parental-leave"))

    row = next(r for r in aggregate(records, curation=read_curation(curation))["misses"]
               if r["key"] == "hr/parental-leave")

    assert row["returned"] is True


def test_a_dismissal_holds_while_nothing_new_arrives(records, tmp_path: Path):
    curation = tmp_path / "curation.json"
    dismiss(curation, "hr/office-plants", count=1)

    result = aggregate(records, curation=read_curation(curation))

    assert "hr/office-plants" not in {r["key"] for r in result["misses"]}
    assert [r["key"] for r in result["dismissed"]] == ["hr/office-plants"]


def test_re_dismissing_a_returned_row_resets_the_baseline(records, tmp_path: Path):
    curation = tmp_path / "curation.json"
    dismiss(curation, "hr/parental-leave", count=2)
    records.append(rec(event="context_gap", session="zzz",
                       domain="hr", topic="parental-leave"))
    dismiss(curation, "hr/parental-leave", count=3)   # seen it, still not writing it

    result = aggregate(records, curation=read_curation(curation))

    assert "hr/parental-leave" not in {r["key"] for r in result["misses"]}


def test_a_legacy_plain_string_dismissal_still_reads(tmp_path: Path):
    """The first version stored bare keys with no baseline."""
    curation = tmp_path / "curation.json"
    curation.write_text(json.dumps({"dismissed": ["hr/x"]}), encoding="utf-8")

    (entry,) = read_curation(curation)["dismissed"]

    assert entry["key"] == "hr/x"
    assert entry["count"] == 0   # unknown baseline: show it again rather than hide it


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

    dismiss(curation, "hr/office-plants", count=1)

    assert json.dumps(records) == before

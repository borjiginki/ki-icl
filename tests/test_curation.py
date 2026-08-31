"""Gaps on the dashboard, and curating the suggestions.

Suggestions arrive filtered only by the agent's judgement, so the panel is useful
only if clearing it is one click. Curation decisions are not telemetry, so they live
in their own file rather than in the append-only log.

Three states, and they differ in what happens when demand arrives afterwards:

- `dismissed`  not now. Comes back if asked for again: a dismissal is a judgement on
               the demand so far, and more demand is new information.
- `resolved`   written up. Comes back, flagged, if it is *still* being missed, which
               means the artifact is not actually reachable and something is broken.
- `deleted`    never want to see this. Confirmed at the UI, and never returns on its
               own. Restorable, so a mistake is recoverable.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.dashboard import aggregate, curate, read_curation  # noqa: E402


def rec(**kw):
    return {"ts": "2026-08-31T08:00:00.000Z", **kw}


@pytest.fixture
def records() -> list[dict]:
    return [
        {"ts": "2026-08-31T07:59:00.000Z", "event": "catalog",
         "artifacts": {"hr/expense-policy": "v1"}},
        rec(event="context_gap", session="aaa", domain="hr", topic="parental-leave"),
        rec(event="context_gap", session="bbb", domain="hr", topic="parental-leave"),
        rec(event="context_gap", session="ccc", domain="hr", topic="office-plants"),
        rec(event="context_use", session="ddd", tool="get_artifact", domain="hr",
            id="onboarding", outcome="not_found"),
    ]


def state_of(records, curation_path):
    return aggregate(records, curation=read_curation(curation_path))


# --- the panel itself -------------------------------------------------------


def test_a_reported_gap_appears_in_what_to_write_next(records):
    rows = {r["key"]: r for r in aggregate(records)["misses"]}

    assert rows["hr/parental-leave"]["count"] == 2
    assert rows["hr/parental-leave"]["sessions"] == 2


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


# --- dismissed --------------------------------------------------------------


def test_a_dismissed_row_leaves_the_main_list(records, tmp_path: Path):
    curation = tmp_path / "c.json"
    curate(curation, "hr/office-plants", "dismissed", count=1)

    result = state_of(records, curation)

    assert "hr/office-plants" not in {r["key"] for r in result["misses"]}
    assert [(r["key"], r["state"]) for r in result["curated"]] == [
        ("hr/office-plants", "dismissed")
    ]


def test_new_demand_after_a_dismissal_brings_the_row_back(records, tmp_path: Path):
    """Somebody asking again is new information, and burying it makes the panel lie
    by omission."""
    curation = tmp_path / "c.json"
    curate(curation, "hr/parental-leave", "dismissed", count=2)
    records.append(rec(event="context_gap", session="zzz",
                       domain="hr", topic="parental-leave"))

    row = next(r for r in state_of(records, curation)["misses"]
               if r["key"] == "hr/parental-leave")

    assert row["returned"] is True
    assert row["was_state"] == "dismissed"


def test_re_dismissing_raises_the_baseline(records, tmp_path: Path):
    curation = tmp_path / "c.json"
    curate(curation, "hr/parental-leave", "dismissed", count=2)
    records.append(rec(event="context_gap", session="zzz",
                       domain="hr", topic="parental-leave"))
    curate(curation, "hr/parental-leave", "dismissed", count=3)

    assert "hr/parental-leave" not in {r["key"] for r in state_of(records, curation)["misses"]}


# --- resolved ---------------------------------------------------------------


def test_a_resolved_row_leaves_the_main_list(records, tmp_path: Path):
    curation = tmp_path / "c.json"
    curate(curation, "hr/parental-leave", "resolved", count=2)

    result = state_of(records, curation)

    assert "hr/parental-leave" not in {r["key"] for r in result["misses"]}
    assert result["curated"][0]["state"] == "resolved"


def test_something_still_missed_after_being_resolved_comes_back(records, tmp_path: Path):
    """You wrote it and people are still missing it, so it is not reachable. That is
    a different and more urgent problem than an unwritten document."""
    curation = tmp_path / "c.json"
    curate(curation, "hr/parental-leave", "resolved", count=2)
    records.append(rec(event="context_gap", session="zzz",
                       domain="hr", topic="parental-leave"))

    row = next(r for r in state_of(records, curation)["misses"]
               if r["key"] == "hr/parental-leave")

    assert row["returned"] is True
    assert row["was_state"] == "resolved"


def test_a_row_says_whether_the_artifact_now_exists(records, tmp_path: Path):
    """Marking something resolved is a claim; the catalog is the evidence."""
    records.append({"ts": "2026-08-31T09:00:00.000Z", "event": "catalog",
                    "artifacts": {"hr/expense-policy": "v1", "hr/parental-leave": "v2"}})

    rows = {r["key"]: r for r in aggregate(records)["misses"]}

    assert rows["hr/parental-leave"]["exists_now"] is True
    assert rows["hr/office-plants"]["exists_now"] is False


# --- deleted ----------------------------------------------------------------


def test_a_deleted_row_leaves_both_working_lists(records, tmp_path: Path):
    """Deleted means gone. Leaving it in the handled list is just a slower dismiss,
    and the confirmation is what makes that safe to mean literally."""
    curation = tmp_path / "c.json"
    curate(curation, "hr/office-plants", "deleted", count=1)

    result = state_of(records, curation)

    assert "hr/office-plants" not in {r["key"] for r in result["misses"]}
    assert "hr/office-plants" not in {r["key"] for r in result["curated"]}


def test_a_deleted_row_never_returns_however_much_demand_arrives(records, tmp_path: Path):
    curation = tmp_path / "c.json"
    curate(curation, "hr/office-plants", "deleted", count=1)
    for n in range(5):
        records.append(rec(event="context_gap", session=f"s{n}",
                           domain="hr", topic="office-plants"))

    result = state_of(records, curation)

    assert "hr/office-plants" not in {r["key"] for r in result["misses"]}
    assert "hr/office-plants" not in {r["key"] for r in result["curated"]}


def test_a_deleted_row_is_still_accounted_for_somewhere(records, tmp_path: Path):
    """Out of the working lists, but not out of existence. A panel that discards a
    signal without saying so is lying by omission, and the operator has no way to
    find out: the mark lives in a file nobody reads."""
    curation = tmp_path / "c.json"
    curate(curation, "hr/office-plants", "deleted", count=1)

    result = state_of(records, curation)

    assert [r["key"] for r in result["suppressed"]] == ["hr/office-plants"]


def test_a_suppressed_row_counts_the_demand_that_arrived_after_deleting(records, tmp_path: Path):
    """Deleting is a judgement made at a moment. Asking again afterwards does not
    undo it, but it is the one fact that would make somebody reconsider."""
    curation = tmp_path / "c.json"
    curate(curation, "hr/parental-leave", "deleted", count=2)
    for n in range(3):
        records.append(rec(event="context_gap", session=f"s{n}",
                           domain="hr", topic="parental-leave"))

    row = next(r for r in state_of(records, curation)["suppressed"]
               if r["key"] == "hr/parental-leave")

    assert row["since"] == 3
    assert row["count"] == 5


def test_a_quiet_deleted_row_reports_no_demand_since(records, tmp_path: Path):
    curation = tmp_path / "c.json"
    curate(curation, "hr/office-plants", "deleted", count=2)

    assert state_of(records, curation)["suppressed"][0]["since"] == 0


def test_only_deleted_rows_are_suppressed(records, tmp_path: Path):
    curation = tmp_path / "c.json"
    curate(curation, "hr/office-plants", "dismissed", count=1)
    curate(curation, "hr/onboarding", "resolved", count=1)

    assert state_of(records, curation)["suppressed"] == []


def test_dismissed_and_resolved_still_appear_in_the_handled_list(records, tmp_path: Path):
    """Only `deleted` vanishes. The other two are decisions you can revisit."""
    curation = tmp_path / "c.json"
    curate(curation, "hr/office-plants", "dismissed", count=1)
    curate(curation, "hr/onboarding", "resolved", count=1)
    curate(curation, "hr/parental-leave", "deleted", count=2)

    handled = {r["key"]: r["state"] for r in state_of(records, curation)["curated"]}

    assert handled == {"hr/office-plants": "dismissed", "hr/onboarding": "resolved"}


def test_a_deleted_row_is_recoverable_by_clearing_the_mark(records, tmp_path: Path):
    """No longer reachable from the UI by design, but the curation file is a plain
    document somebody can edit when they regret it."""
    curation = tmp_path / "c.json"
    curate(curation, "hr/office-plants", "deleted", count=1)
    curate(curation, "hr/office-plants", "active")

    assert "hr/office-plants" in {r["key"] for r in state_of(records, curation)["misses"]}


# --- the file ---------------------------------------------------------------


def test_curating_the_same_key_twice_replaces_rather_than_duplicates(tmp_path: Path):
    curation = tmp_path / "c.json"
    curate(curation, "hr/x", "dismissed", count=1)
    curate(curation, "hr/x", "resolved", count=1)

    entries = read_curation(curation)["entries"]
    assert len(entries) == 1
    assert entries[0]["state"] == "resolved"


def test_setting_active_removes_the_entry_entirely(tmp_path: Path):
    curation = tmp_path / "c.json"
    curate(curation, "hr/x", "deleted", count=1)
    curate(curation, "hr/x", "active")

    assert read_curation(curation)["entries"] == []


def test_an_unknown_state_is_refused_rather_than_written(tmp_path: Path):
    curation = tmp_path / "c.json"

    with pytest.raises(ValueError):
        curate(curation, "hr/x", "banished", count=1)

    assert read_curation(curation)["entries"] == []


def test_a_legacy_dismissed_list_still_reads(tmp_path: Path):
    """Two earlier shapes: bare keys, then {key,count,at} under a `dismissed` key."""
    curation = tmp_path / "c.json"
    curation.write_text(json.dumps({"dismissed": [
        "hr/bare",
        {"key": "hr/withcount", "count": 3, "at": "2026-08-31T09:00:00.000Z"},
    ]}), encoding="utf-8")

    entries = {e["key"]: e for e in read_curation(curation)["entries"]}

    assert entries["hr/bare"]["state"] == "dismissed"
    assert entries["hr/bare"]["count"] == 0     # unknown baseline: show it again
    assert entries["hr/withcount"]["count"] == 3


def test_a_missing_or_corrupt_curation_file_reads_as_nothing_curated(tmp_path: Path):
    assert read_curation(tmp_path / "nope.json")["entries"] == []

    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert read_curation(broken)["entries"] == []


def test_curation_does_not_touch_the_usage_log(records, tmp_path: Path):
    """Curation is a decision, not an observation. The log stays append-only."""
    before = json.dumps(records)

    curate(tmp_path / "c.json", "hr/office-plants", "deleted", count=1)

    assert json.dumps(records) == before

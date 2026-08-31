"""Dashboard aggregation. All the logic lives here; the HTML only renders.

Records are deliberately a mix of old (pre-correlation) and new shapes, because a
log written before the telemetry change must still render.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.dashboard import aggregate, filter_records, read_records  # noqa: E402


def rec(**kw):
    base = {"ts": "2026-08-31T08:00:00.000Z", "event": "context_use"}
    return {**base, **kw}


@pytest.fixture
def records() -> list[dict]:
    """Three sessions: one fetches, one browses and leaves, one misses twice."""
    return [
        {
            "ts": "2026-08-31T07:59:00.000Z",
            "event": "catalog",
            "domain_count": 1,
            "artifact_count": 2,
            "bytes": 7361,
            "artifacts": {"hr/expense-policy": "v1", "hr/discovery-workshop": "v1"},
        },
        rec(session="aaa", seq=1, tool="list_domains", outcome="found", domain_count=1, duration_ms=0.6),
        rec(session="aaa", seq=2, tool="get_domain_manifest", domain="hr", outcome="found",
            artifact_count=2, offered=["discovery-workshop", "expense-policy"], duration_ms=0.4),
        rec(session="aaa", seq=3, tool="get_artifact", domain="hr", id="expense-policy",
            outcome="found", version_id="v1", file_count=2, bytes=2270, skipped=0, duration_ms=0.9),
        rec(session="bbb", seq=1, tool="list_domains", outcome="found", domain_count=1, duration_ms=0.5),
        rec(session="bbb", seq=2, tool="get_domain_manifest", domain="hr", outcome="found",
            artifact_count=2, offered=["discovery-workshop", "expense-policy"], duration_ms=0.3),
        rec(session="ccc", seq=1, tool="get_artifact", domain="hr", id="parental-leave",
            outcome="not_found", duration_ms=0.2),
        rec(session="ccc", seq=2, tool="get_artifact", domain="hr", id="parental-leave",
            outcome="not_found", duration_ms=0.2),
        rec(session="ccc", seq=3, tool="get_artifact", domain="hr", id="expense-policy",
            outcome="found", version_id="v2", file_count=2, bytes=2400, skipped=1, duration_ms=3.1),
        rec(session="ccc", seq=4, tool="list_domains", outcome="error",
            error="ToolError: blob unreachable", duration_ms=0.1),
        # An old record, written before correlation existed.
        {"ts": "2026-08-30T10:00:00Z", "event": "context_use", "tool": "get_artifact",
         "domain": "hr", "id": "discovery-workshop", "outcome": "found",
         "version_id": "v1", "file_count": 3, "duration_ms": 0.7},
    ]


# --- panel 1: what to write next --------------------------------------------


def test_misses_are_ranked_by_demand(records):
    misses = aggregate(records)["misses"]

    assert misses[0]["key"] == "hr/parental-leave"
    assert misses[0]["count"] == 2
    assert misses[0]["last"] == "2026-08-31T08:00:00.000Z"


def test_a_miss_says_whether_the_id_ever_existed(records):
    """A miss for something deleted is a different problem from one that never was."""
    (miss,) = aggregate(records)["misses"]

    assert miss["ever_existed"] is False


def test_an_id_that_was_in_the_catalog_but_missed_is_flagged_as_deleted(records):
    records.append(rec(session="ddd", tool="get_artifact", domain="hr",
                       id="discovery-workshop", outcome="not_found"))

    by_key = {m["key"]: m for m in aggregate(records)["misses"]}

    assert by_key["hr/discovery-workshop"]["ever_existed"] is True


def test_no_misses_is_an_empty_list_not_an_error():
    assert aggregate([rec(tool="list_domains", outcome="found")])["misses"] == []


def test_a_request_for_a_domain_that_does_not_exist_is_a_miss_too(records):
    """An unknown-domain record carries no `id`, so it fell through every panel and
    was visible only in the filter dropdown."""
    records.append(rec(session="ddd", tool="get_domain_manifest",
                       domain="compnay", outcome="not_found"))

    misses = {m["key"]: m for m in aggregate(records)["misses"]}

    assert misses["compnay"]["count"] == 1
    assert misses["compnay"]["ever_existed"] is False


def test_the_domain_filter_offers_only_domains_that_exist(records):
    """A mistyped domain is a fact about the past, not a place to filter to."""
    records.append(rec(session="ddd", tool="get_domain_manifest",
                       domain="compnay", outcome="not_found"))

    assert aggregate(records)["domains"] == ["hr"]


def test_the_domain_filter_falls_back_to_the_log_when_no_catalog_was_recorded():
    """A log written before catalog records existed still needs a usable filter."""
    calls = [rec(tool="get_artifact", domain="hr", id="x", outcome="found")]

    assert aggregate(calls)["domains"] == ["hr"]


# --- panel 2: usage and hit rate --------------------------------------------


def test_headline_counts(records):
    kpi = aggregate(records)["kpi"]

    assert kpi["lookups"] == 5          # get_artifact entries, hits + misses
    assert kpi["hits"] == 3
    assert kpi["hit_rate"] == 60.0
    assert kpi["sessions"] == 3         # the old record carries no session
    assert kpi["bytes"] == 4670
    assert kpi["errors"] == 1


def test_artifacts_served_is_ranked(records):
    served = aggregate(records)["served"]

    assert served[0]["key"] == "hr/expense-policy"
    assert served[0]["count"] == 2
    assert served[0]["bytes"] == 4670


def test_the_funnel_counts_sessions_at_each_step(records):
    """A session that browsed and never fetched means the descriptions did not
    let it choose."""
    funnel = aggregate(records)["funnel"]

    assert funnel["listed"] == 2        # aaa, bbb
    assert funnel["manifested"] == 2    # aaa, bbb
    assert funnel["fetched"] == 1       # aaa only; bbb left after browsing


def test_a_session_that_fetched_without_discovering_is_counted_separately(records):
    """ccc knew the id already. Counting it as "fetched" would hide the drop-off
    the funnel exists to show."""
    funnel = aggregate(records)["funnel"]

    assert funnel["direct"] == 1


def test_an_errored_call_does_not_advance_a_funnel_step(records):
    """ccc's list_domains errored, so it never got a list to choose from."""
    assert aggregate(records)["funnel"]["listed"] == 2


def test_chosen_against_offered(records):
    """expense-policy was offered twice and fetched once; discovery-workshop was
    offered twice and never fetched from a manifest session."""
    offered = {row["key"]: row for row in aggregate(records)["offered"]}

    assert offered["hr/expense-policy"]["offered"] == 2
    assert offered["hr/expense-policy"]["chosen"] == 1
    assert offered["hr/discovery-workshop"]["offered"] == 2
    assert offered["hr/discovery-workshop"]["chosen"] == 0


def test_activity_is_bucketed_by_hour(records):
    activity = aggregate(records)["activity"]

    assert [b["hour"] for b in activity][-1] == "2026-08-31T08"
    assert sum(b["found"] + b["missed"] for b in activity) == 5


# --- panel 3: versions ------------------------------------------------------


def test_versions_served_are_listed_per_artifact(records):
    versions = {v["key"]: v for v in aggregate(records)["versions"]}

    assert {s["version_id"] for s in versions["hr/expense-policy"]["served"]} == {"v1", "v2"}
    assert versions["hr/expense-policy"]["changed"] is True
    assert versions["hr/discovery-workshop"]["changed"] is False


# --- panel 4: performance ---------------------------------------------------


def test_latency_percentiles_per_tool(records):
    latency = {row["tool"]: row for row in aggregate(records)["latency"]}

    assert latency["get_artifact"]["max"] == 3.1
    assert latency["get_artifact"]["n"] == 5
    assert latency["list_domains"]["p50"] <= latency["list_domains"]["max"]


def test_a_percentile_never_exceeds_the_largest_observed_value():
    """statistics.quantiles defaults to the exclusive method, which EXTRAPOLATES past
    the data. A p95 above max is not a number that can be defended to anyone."""
    calls = [rec(tool="get_artifact", outcome="found", duration_ms=d)
             for d in [0.3, 0.4, 0.5, 0.5, 0.6, 19.4]]

    (row,) = aggregate(calls)["latency"]

    assert row["p95"] <= row["max"] == 19.4
    assert row["p50"] >= 0.3


def test_a_single_observation_is_its_own_percentile():
    (row,) = aggregate([rec(tool="list_domains", outcome="found", duration_ms=2.0)])["latency"]

    assert row["p50"] == row["p95"] == row["max"] == 2.0


def test_a_failed_call_is_left_out_of_the_latency_distribution(records):
    """A call that raised is not a measurement of service time, and one slow failure
    drags p95 far enough to flatten every other bar on the chart."""
    records.append(rec(session="eee", tool="get_domain_manifest", domain="hr",
                       outcome="error", error="ToolError: boom", duration_ms=980.0))

    latency = {row["tool"]: row for row in aggregate(records)["latency"]}

    assert latency["get_domain_manifest"]["max"] < 1.0
    assert latency["get_domain_manifest"]["n"] == 2


def test_duration_against_bytes_is_paired_for_found_fetches_only(records):
    points = aggregate(records)["size_vs_time"]

    assert {(p["bytes"], p["duration_ms"]) for p in points} == {(2270, 0.9), (2400, 3.1)}


# --- robustness -------------------------------------------------------------


def test_an_empty_log_aggregates_without_raising():
    result = aggregate([])

    assert result["kpi"]["lookups"] == 0
    assert result["kpi"]["hit_rate"] == 0
    assert result["misses"] == []


def test_a_corrupt_line_is_skipped_rather_than_killing_the_read(tmp_path: Path):
    log = tmp_path / "usage.jsonl"
    log.write_text(
        '{"event":"context_use","tool":"list_domains","outcome":"found"}\n'
        "not json at all\n"
        '{"event":"context_use","tool":"list_domains","outcome":"found"}\n',
        encoding="utf-8",
    )

    assert len(read_records(log)) == 2


def test_a_missing_log_reads_as_empty(tmp_path: Path):
    assert read_records(tmp_path / "nope.jsonl") == []


# --- filters ----------------------------------------------------------------


def test_filtering_by_domain_keeps_only_that_domain(records):
    records.append(rec(session="zzz", tool="get_artifact", domain="finance",
                       id="budget", outcome="found", version_id="v1", bytes=10))

    kept = filter_records(records, domain="finance", hours=0)

    assert {r.get("domain") for r in kept if r.get("domain")} == {"finance"}


def test_filtering_by_domain_keeps_the_catalog_so_deletions_stay_detectable(records):
    """Drop the catalog and every miss suddenly reads as "never written"."""
    kept = filter_records(records, domain="hr", hours=0)

    assert any(r.get("event") == "catalog" for r in kept)


def test_filtering_by_hours_drops_older_records(records):
    """The old pre-correlation record is a day earlier than the rest."""
    kept = filter_records(records, domain="", hours=1, now="2026-08-31T08:30:00.000Z")

    assert all(r.get("ts", "") >= "2026-08-31T07:30" for r in kept)
    assert len(kept) < len(records)


def test_no_filter_is_the_identity(records):
    assert filter_records(records, domain="", hours=0) == records

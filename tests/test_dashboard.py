"""Dashboard aggregation. All the logic lives here; the HTML only renders.

Records are deliberately a mix of old (pre-correlation) and new shapes, because a
log written before the telemetry change must still render.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.dashboard import aggregate, filter_records, read_records  # noqa: E402


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


# --- the catalog, when the log has no snapshot of it -------------------------


def test_what_exists_now_is_read_from_disk_not_only_from_the_log(tmp_path: Path):
    """The log's catalog record is emitted once, at server start. Clearing the log
    (routine, between tests) therefore silently killed `now written`, `existed, now
    gone` and the header count until the MCP server happened to restart. What exists
    now is a question about the tree, so ask the tree."""
    records = [
        {"ts": "2026-08-31T08:00:00.000Z", "event": "context_gap",
         "session": "aaa", "domain": "hr", "topic": "expense-policy"},
    ]

    result = aggregate(records, catalog={"artifacts": {"hr/expense-policy": "v1"},
                                         "artifact_count": 1, "domain_count": 1, "bytes": 10})

    assert result["misses"][0]["exists_now"] is True
    assert result["catalog"]["artifact_count"] == 1


def test_a_catalog_read_from_disk_wins_over_a_stale_one_in_the_log(tmp_path: Path):
    records = [
        {"ts": "2026-08-31T07:00:00.000Z", "event": "catalog", "artifacts": {}},
        {"ts": "2026-08-31T08:00:00.000Z", "event": "context_gap",
         "session": "aaa", "domain": "hr", "topic": "expense-policy"},
    ]

    result = aggregate(records, catalog={"artifacts": {"hr/expense-policy": "v1"}})

    assert result["misses"][0]["exists_now"] is True


def test_history_still_comes_from_the_log(tmp_path: Path):
    """`ever_existed` is the one thing the tree cannot answer: it means the artifact
    was there and is not any more, which only the old snapshots record."""
    records = [
        {"ts": "2026-08-31T07:00:00.000Z", "event": "catalog",
         "artifacts": {"hr/retired-thing": "v1"}},
        {"ts": "2026-08-31T08:00:00.000Z", "event": "context_use", "session": "aaa",
         "tool": "get_artifact", "domain": "hr", "id": "retired-thing",
         "outcome": "not_found"},
    ]

    row = aggregate(records, catalog={"artifacts": {}})["misses"][0]

    assert row["ever_existed"] is True
    assert row["exists_now"] is False


def test_no_catalog_anywhere_is_not_an_error(tmp_path: Path):
    result = aggregate([{"ts": "2026-08-31T08:00:00.000Z", "event": "context_gap",
                         "session": "aaa", "domain": "hr", "topic": "x"}])

    assert result["catalog"] is None
    assert result["misses"][0]["exists_now"] is False


def test_the_dashboard_serves_a_live_catalog_when_run_as_a_script(tmp_path: Path):
    """Run as `python scripts/dashboard.py`, only scripts/ is on sys.path, so the
    `server.*` import inside `live_catalog` fails and is swallowed by its own
    fallback. Nothing in-process catches that, because pytest puts the repo root on
    the path itself. Only launching it the way the Makefile does will.

    Needs a real packaged tree with at least one artifact in it - domains/ moved to
    ki-ccl, so this reads dist/staging from a sibling checkout, packaged on the fly,
    and skips cleanly when that checkout is not present. See load_real_policy in
    conftest.py for the same pattern.
    """
    import socket
    import subprocess
    import time
    import urllib.request

    from tests.conftest import CCL_ROOT

    if not (CCL_ROOT / "scripts" / "package_context.py").is_file():
        pytest.skip(f"no ki-ccl checkout at {CCL_ROOT} (set KI_CCL_ROOT to override)")

    repo = Path(__file__).resolve().parent.parent
    staging = tmp_path / "staging"
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); "
            "from scripts.package_context import build; "
            "build(Path(sys.argv[1]), Path(sys.argv[2]) / 'out', Path(sys.argv[2]))",
            str(CCL_ROOT),
            str(staging),
        ],
        check=True,
    )

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    log = tmp_path / "usage.jsonl"
    log.write_text("", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, str(repo / "scripts" / "dashboard.py")],
        env={**os.environ, "DASHBOARD_PORT": str(port),
             "CONTEXT_ROOT": str(staging),
             "CONTEXT_USAGE_LOG": str(log),
             "CONTEXT_CURATION": str(tmp_path / "curation.json"),
             "NO_BROWSER": "1"},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        payload = None
        for _ in range(50):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/data", timeout=1) as r:
                    payload = json.loads(r.read())
                break
            except Exception:  # noqa: BLE001 — still starting
                time.sleep(0.1)
        assert payload is not None, "dashboard never came up"
        # An empty log has no catalog record at all, so a catalog here can only have
        # come from reading the tree.
        assert payload["catalog"] is not None
        assert payload["catalog"]["artifact_count"] > 0
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_the_page_addresses_its_endpoints_relative_to_where_it_is_served():
    """Served at / by scripts/dashboard.py and at /dashboard by the MCP server.

    An absolute path works only for the first, and the failure mode is quiet: the page
    renders its whole layout and then shows no data, because the fetch 404'd against a
    route that does not exist under that prefix.
    """
    page = (Path(__file__).resolve().parent.parent / "server" / "dashboard.html").read_text(
        encoding="utf-8"
    )

    assert 'const BASE = location.pathname.replace(/\\/+$/, "");' in page
    assert 'fetch(BASE + "/data?" + q)' in page
    assert 'fetch(BASE + (state === "purge" ? "/purge" : "/curate")' in page
    assert 'fetch("/' not in page, "an absolute fetch cannot work under a path prefix"


def test_an_unset_or_blank_usage_log_variable_lands_on_the_file_the_writer_uses():
    """server/usage.py writes the log and server/dashboard.py reads it, so the two
    resolving CONTEXT_USAGE_LOG differently is a page that renders in full over zero
    records with nothing anywhere saying why.

    Blank is the value that broke it: `Path("")` is `Path(".")`, which is not a file,
    so `read_records` returned nothing forever. A trailing space did the same, and it
    is invisible in the portal field the value is typed into.
    """
    from server import dashboard, usage

    if os.environ.get("CONTEXT_USAGE_LOG"):
        pytest.skip("CONTEXT_USAGE_LOG is set, so usage.USAGE_LOG_PATH is not its default")

    # usage.py's default is relative to the working directory and this module's is
    # anchored on the repository root, which is the same file wherever this actually
    # runs. Joining it here pins the two defaults together without pinning the test to
    # a working directory.
    writer_default = dashboard.ROOT / usage.USAGE_LOG_PATH

    assert dashboard.LOG == writer_default
    for unset_or_blank in (None, "", "   "):
        assert dashboard._usage_log_path(unset_or_blank) == writer_default
    assert dashboard._usage_log_path(" /var/log/usage.jsonl ") == Path("/var/log/usage.jsonl")


def test_the_loopback_host_answers_400_to_a_body_that_is_not_an_object():
    """The two hosts must not disagree about which requests are a 400.

    `[1, 2]` reached `.get` on a list here, which `except (ValueError, OSError)` does
    not catch, so the caller got a dropped connection where the mounted host answered
    400. Both hosts parse through `body_key_and_state` now.
    """
    import threading
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer

    from scripts.dashboard import Handler

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(
                urllib.request.Request(
                    f"http://127.0.0.1:{server.server_port}/curate",
                    data=b"[1, 2]",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                ),
                timeout=5,
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert refused.value.code == 400

"""Deleting a suggestion for good.

Dismiss and resolve are filters over the log: the demand is still there, and the mark
says what you decided about it. Delete cannot work that way. A filter that must hold
forever is a tombstone, it accumulates, it is invisible, and it silently swallows the
next person who asks for the same thing.

So delete goes at the source. It removes the records that produced the suggestion, and
appends one `purge` record saying what it removed, which keeps the log an honest
account of itself. Nothing is left to remember, so nothing can be silently suppressed:
if somebody asks again afterwards, the suggestion comes back as a new one, because it
earned its place again.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.dashboard import (  # noqa: E402
    aggregate,
    curate,
    purge,
    read_curation,
    read_records,
)


def write_log(path: Path, records: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return path


def rec(**kw):
    return {"ts": "2026-08-31T08:00:00.000Z", **kw}


def sample() -> list[dict]:
    return [
        {"ts": "2026-08-31T07:59:00.000Z", "event": "catalog",
         "artifacts": {"hr/expense-policy": "v1"}},
        rec(event="context_gap", session="aaa", domain="hr", topic="office-plants"),
        rec(event="context_gap", session="bbb", domain="hr", topic="office-plants"),
        rec(event="context_gap", session="ccc", domain="hr", topic="parental-leave"),
        rec(event="context_use", session="ddd", tool="get_artifact", domain="hr",
            id="office-plants", outcome="not_found"),
        rec(event="context_use", session="ddd", tool="get_artifact", domain="hr",
            id="expense-policy", outcome="found", bytes=2270, duration_ms=0.7),
        rec(event="context_use", session="ddd", tool="list_domains", outcome="found"),
    ]


# --- what it removes --------------------------------------------------------


def test_purging_removes_the_reported_gaps_for_that_key(tmp_path: Path):
    log = write_log(tmp_path / "usage.jsonl", sample())

    purge(log, tmp_path / "c.json", "hr/office-plants")

    gaps = [r for r in read_records(log) if r.get("event") == "context_gap"]
    assert [r["topic"] for r in gaps] == ["parental-leave"]


def test_purging_removes_the_guessed_misses_for_that_key(tmp_path: Path):
    log = write_log(tmp_path / "usage.jsonl", sample())

    purge(log, tmp_path / "c.json", "hr/office-plants")

    assert not [
        r for r in read_records(log)
        if r.get("id") == "office-plants" and r.get("outcome") == "not_found"
    ]


def test_purging_leaves_every_other_record_alone(tmp_path: Path):
    """Only the demand for that one key goes. Hits, other tools, the catalog
    snapshot and every other suggestion are untouched."""
    log = write_log(tmp_path / "usage.jsonl", sample())

    purge(log, tmp_path / "c.json", "hr/office-plants")

    kept = [r for r in read_records(log) if r.get("event") != "purge"]
    assert kept == [r for r in sample() if "office-plants" not in json.dumps(r)]


def test_purging_a_domain_level_miss_uses_the_bare_domain_as_the_key(tmp_path: Path):
    """A request for a domain that does not exist carries no id, so its key is just
    the domain, and purging must match on that rather than on `domain/None`."""
    log = write_log(tmp_path / "usage.jsonl", [
        rec(event="context_use", session="aaa", tool="get_domain_manifest",
            domain="finance", outcome="not_found"),
        rec(event="context_gap", session="bbb", domain="hr", topic="parental-leave"),
    ])

    assert purge(log, tmp_path / "c.json", "finance")["removed"] == 1
    assert [r.get("topic") for r in read_records(log)
            if r.get("event") == "context_gap"] == ["parental-leave"]


def test_purging_an_unknown_key_removes_nothing(tmp_path: Path):
    log = write_log(tmp_path / "usage.jsonl", sample())

    assert purge(log, tmp_path / "c.json", "hr/nothing-like-this")["removed"] == 0
    assert len([r for r in read_records(log) if r.get("event") != "purge"]) == len(sample())


# --- what it leaves behind --------------------------------------------------


def test_purging_records_what_it_removed(tmp_path: Path):
    """The log stops being append-only here, so it has to say so itself. Otherwise
    counts change with no explanation anywhere."""
    log = write_log(tmp_path / "usage.jsonl", sample())

    purge(log, tmp_path / "c.json", "hr/office-plants")

    audit = [r for r in read_records(log) if r.get("event") == "purge"]
    assert len(audit) == 1
    assert audit[0]["key"] == "hr/office-plants"
    assert audit[0]["removed"] == 3
    assert audit[0]["ts"]


def test_a_purge_record_is_not_itself_a_suggestion(tmp_path: Path):
    """It carries a domain and an id-shaped key. It must not read as demand and
    resurrect the very row it recorded the removal of."""
    log = write_log(tmp_path / "usage.jsonl", sample())
    purge(log, tmp_path / "c.json", "hr/office-plants")

    result = aggregate(read_records(log))

    assert "hr/office-plants" not in {r["key"] for r in result["misses"]}


def test_purging_clears_any_mark_that_key_had(tmp_path: Path):
    """The mark is about a row that no longer exists. Leaving it behind is exactly
    the invisible accumulating state purging exists to avoid."""
    log = write_log(tmp_path / "usage.jsonl", sample())
    curation = tmp_path / "c.json"
    curate(curation, "hr/office-plants", "dismissed", count=3)

    purge(log, curation, "hr/office-plants")

    assert read_curation(curation)["entries"] == []


def test_purging_leaves_other_keys_marks_alone(tmp_path: Path):
    log = write_log(tmp_path / "usage.jsonl", sample())
    curation = tmp_path / "c.json"
    curate(curation, "hr/parental-leave", "dismissed", count=1)

    purge(log, curation, "hr/office-plants")

    assert [e["key"] for e in read_curation(curation)["entries"]] == ["hr/parental-leave"]


# --- the point of doing it this way -----------------------------------------


def test_a_purged_row_is_gone_from_every_list(tmp_path: Path):
    log = write_log(tmp_path / "usage.jsonl", sample())
    curation = tmp_path / "c.json"

    purge(log, curation, "hr/office-plants")
    result = aggregate(read_records(log), curation=read_curation(curation))

    assert "hr/office-plants" not in {r["key"] for r in result["misses"]}
    assert "hr/office-plants" not in {r["key"] for r in result["curated"]}


def test_asking_again_after_a_purge_starts_a_fresh_suggestion(tmp_path: Path):
    """The one behaviour a tombstone cannot have. Nothing is remembered, so a new
    request is new demand rather than something silently swallowed."""
    log = write_log(tmp_path / "usage.jsonl", sample())
    curation = tmp_path / "c.json"
    purge(log, curation, "hr/office-plants")

    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(rec(ts="2026-08-31T09:00:00.000Z", event="context_gap",
                                    session="zzz", domain="hr", topic="office-plants")) + "\n")

    row = next(r for r in aggregate(read_records(log), curation=read_curation(curation))["misses"]
               if r["key"] == "hr/office-plants")

    assert row["count"] == 1
    assert not row.get("returned")


def test_purging_survives_a_corrupt_line(tmp_path: Path):
    """`read_records` skips unparseable lines, and a rewrite must not silently drop
    them: the file belongs to whoever wrote it, not to this tool."""
    log = tmp_path / "usage.jsonl"
    log.write_text(
        json.dumps(rec(event="context_gap", domain="hr", topic="office-plants")) + "\n"
        + "{not json\n"
        + json.dumps(rec(event="context_gap", domain="hr", topic="parental-leave")) + "\n",
        encoding="utf-8",
    )

    purge(log, tmp_path / "c.json", "hr/office-plants")

    assert "{not json" in log.read_text(encoding="utf-8")


def test_purging_a_missing_log_is_a_no_op(tmp_path: Path):
    assert purge(tmp_path / "nope.jsonl", tmp_path / "c.json", "hr/x")["removed"] == 0

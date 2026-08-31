"""The read path: list_domains, get_domain_manifest, get_artifact.

These tests pin spec section 2.3's non-negotiables. The honest-miss and
path-traversal tests exist so review never has to re-argue them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


# --- list_domains -----------------------------------------------------------


def test_list_domains_lists_every_domain(artifacts):
    payload = artifacts.list_domains_payload()

    assert [d["id"] for d in payload["domains"]] == ["company"]
    company = payload["domains"][0]
    assert company["description"] == "How we work and what we offer."
    assert company["kind"] == "governed"
    assert company["owner"] is None
    assert company["artifact_count"] == 2
    assert "fetch_hint" in payload


def test_list_domains_on_an_empty_catalog_returns_an_empty_list(
    artifacts, tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(artifacts, "ARTIFACTS_ROOT", tmp_path / "nothing-here")

    payload = artifacts.list_domains_payload()

    assert payload["domains"] == []


def test_a_domain_without_a_manifest_is_skipped(artifacts, catalog: Path):
    (catalog / "domains" / "half-built").mkdir()

    assert [d["id"] for d in artifacts.list_domains_payload()["domains"]] == ["company"]


# --- get_domain_manifest ----------------------------------------------------


def test_manifest_has_one_row_per_artifact_and_no_file_bodies(artifacts):
    payload = artifacts.domain_manifest_payload("company")

    assert [a["id"] for a in payload["artifacts"]] == [
        "discovery-workshop",
        "expense-policy",
    ]
    for row in payload["artifacts"]:
        assert row["version_id"]
        assert "files" not in row
    assert "fetch_hint" in payload


def test_an_empty_domain_is_served_honestly_and_points_at_report_gap(artifacts, catalog):
    """Five of the seven real domains hold nothing yet, so this is a normal answer.

    Telling an agent to `get_artifact` from an empty domain is a dead end; the gap is
    the only useful thing it can do, and the only way the domain learns it is wanted.
    """
    empty = catalog / "domains" / "marketing"
    empty.mkdir()
    (empty / "_manifest.json").write_text(
        json.dumps({"domain": "marketing", "description": "Awareness.", "artifacts": []}),
        encoding="utf-8",
    )

    payload = artifacts.domain_manifest_payload("marketing")

    assert payload["artifacts"] == []
    assert "report_gap" in payload["fetch_hint"]
    assert "get_artifact" not in payload["fetch_hint"]


def test_the_manifest_warns_when_a_row_is_not_approved(artifacts):
    """The caveat has to travel with the row, not sit in file bodies.

    A cross-artifact question is answered from the manifest without fetching, so an
    agent following that path never sees a "demo content" banner inside a file. The
    fixture's rows are unreviewed, which is the normal state of this corpus.
    """
    hint = artifacts.domain_manifest_payload("company")["fetch_hint"]

    assert "NOT APPROVED" in hint
    assert "expense-policy" in hint and "discovery-workshop" in hint
    assert "never present `demo` content as fact" in hint


def test_an_approved_domain_gets_no_caveat(artifacts, catalog):
    """The warning must be absent when it does not apply, or it becomes noise."""
    path = catalog / "domains" / "company" / "_manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    for row in manifest["artifacts"]:
        row["review"] = "approved"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    hint = artifacts.domain_manifest_payload("company")["fetch_hint"]

    assert "NOT APPROVED" not in hint
    assert "get_artifact" in hint


def test_get_artifact_carries_the_review_state(artifacts):
    entry = artifacts.get_artifact_payload("company", ["expense-policy"])["artifacts"][0]

    assert "review" in entry


def test_unknown_domain_manifest_is_not_found_and_names_the_real_domains(artifacts):
    payload = artifacts.domain_manifest_payload("compnay")

    assert payload == {"status": "not_found", "domain": "compnay", "known": ["company"]}


# --- get_artifact -----------------------------------------------------------


def test_exact_hit_returns_every_file_and_its_metadata(artifacts):
    payload = artifacts.get_artifact_payload(
        "company", ["expense-policy"], max_file_bytes=1_048_576
    )

    (found,) = payload["artifacts"]
    assert found["status"] == "found"
    assert found["id"] == "expense-policy"
    assert found["title"] == "Expense policy"
    assert found["kind"] == "guideline"
    assert found["version_id"] == "aaaa1111"
    assert found["file_count"] == 2
    readme = next(f for f in found["files"] if f["path"] == "README.md")
    assert readme["encoding"] == "utf-8"
    assert readme["mime_type"] == "text/markdown"
    assert "Receipts within 30 days." in readme["content"]
    assert found["skipped_files"] == []


def test_a_miss_is_honest_and_carries_no_suggestion(artifacts):
    """One character off a real id. The whole value of this system is that a
    wrong answer is impossible, so a miss must never be a near match."""
    payload = artifacts.get_artifact_payload(
        "company", ["expense-polcy"], max_file_bytes=1_048_576
    )

    (miss,) = payload["artifacts"]
    assert miss == {"status": "not_found", "id": "expense-polcy"}


def test_a_batch_degrades_per_item(artifacts):
    payload = artifacts.get_artifact_payload(
        "company", ["expense-policy", "does-not-exist"], max_file_bytes=1_048_576
    )

    statuses = [(a["id"], a["status"]) for a in payload["artifacts"]]
    assert statuses == [("expense-policy", "found"), ("does-not-exist", "not_found")]


def test_a_bare_string_id_behaves_like_a_one_element_list(artifacts):
    bare = artifacts.get_artifact_payload("company", "expense-policy", max_file_bytes=1_048_576)
    listed = artifacts.get_artifact_payload(
        "company", ["expense-policy"], max_file_bytes=1_048_576
    )

    assert bare == listed


def test_unknown_domain_on_get_artifact_is_not_found_with_no_artifacts_key(artifacts):
    payload = artifacts.get_artifact_payload("company ", ["expense-policy"], max_file_bytes=1_048_576)

    assert payload == {"status": "not_found", "domain": "company ", "known": ["company"]}
    assert "artifacts" not in payload


@pytest.mark.parametrize(
    "hostile_id",
    ["../../etc/passwd", "../company/expense-policy", "/etc/passwd", "expense-policy/../.."],
)
def test_path_traversal_is_a_miss_and_reads_nothing(artifacts, hostile_id: str):
    payload = artifacts.get_artifact_payload("company", [hostile_id], max_file_bytes=1_048_576)

    (entry,) = payload["artifacts"]
    assert entry == {"status": "not_found", "id": hostile_id}


def test_an_oversized_file_is_skipped_and_named(artifacts, catalog: Path):
    big = catalog / "domains" / "company" / "expense-policy" / "big.txt"
    big.write_text("x" * 5000, encoding="utf-8")

    payload = artifacts.get_artifact_payload("company", ["expense-policy"], max_file_bytes=1000)

    (found,) = payload["artifacts"]
    assert found["skipped_files"] == ["big.txt"]
    assert "big.txt" not in [f["path"] for f in found["files"]]


def test_nested_supporting_files_are_returned_with_relative_paths(artifacts, catalog: Path):
    nested = catalog / "domains" / "company" / "expense-policy" / "annex" / "rates.csv"
    nested.parent.mkdir()
    nested.write_text("year,cap\n2026,25\n", encoding="utf-8")

    payload = artifacts.get_artifact_payload(
        "company", ["expense-policy"], max_file_bytes=1_048_576
    )

    (found,) = payload["artifacts"]
    assert "annex/rates.csv" in [f["path"] for f in found["files"]]


def test_visible_domains_is_the_only_scoping_seam(artifacts, monkeypatch):
    """Every read path must resolve domains through visible_domains(), so that
    per-identity scoping later has exactly one place to change."""
    monkeypatch.setattr(artifacts, "visible_domains", lambda: [])

    assert artifacts.list_domains_payload()["domains"] == []
    assert artifacts.domain_manifest_payload("company")["status"] == "not_found"
    assert artifacts.get_artifact_payload(
        "company", ["expense-policy"], max_file_bytes=1_048_576
    )["status"] == "not_found"

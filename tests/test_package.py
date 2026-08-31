"""Packaging: the archive, the per-domain manifest, and the version stamp."""

from __future__ import annotations

import json
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.package_context import build  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent


def _manifest_in(stage: Path, domain: str) -> dict:
    return json.loads((stage / "domains" / domain / "_manifest.json").read_text())


def test_it_writes_an_archive_and_an_informational_manifest(source_tree: Path, tmp_path: Path):
    out, stage = tmp_path / "dist", tmp_path / "stage"

    summary = build(source_tree, out, stage)

    assert sorted(p.name for p in out.iterdir()) == ["context.tar.gz", "manifest.json"]
    assert summary["domain_count"] == 1
    assert summary["artifact_count"] == 2
    assert json.loads((out / "manifest.json").read_text())["archive"] == "context.tar.gz"


def test_the_archive_is_rooted_at_domains(source_tree: Path, tmp_path: Path):
    out, stage = tmp_path / "dist", tmp_path / "stage"
    build(source_tree, out, stage)

    with tarfile.open(out / "context.tar.gz") as tar:
        names = tar.getnames()

    assert "domains/company/expense-policy/README.md" in names
    assert "domains/company/_manifest.json" in names
    assert all(n == "domains" or n.startswith("domains/") for n in names)


def test_each_domain_manifest_is_sorted_by_id_and_stamps_every_artifact(
    source_tree: Path, tmp_path: Path
):
    out, stage = tmp_path / "dist", tmp_path / "stage"
    build(source_tree, out, stage)

    manifest = _manifest_in(stage, "company")

    assert manifest["domain"] == "company"
    assert manifest["description"] == "How we work and what we offer."
    assert manifest["owner"] is None
    assert [a["id"] for a in manifest["artifacts"]] == ["discovery-workshop", "expense-policy"]
    for row in manifest["artifacts"]:
        assert row["version_id"]
        assert set(row) == {
            "id", "title", "kind", "description", "class", "owner", "version_id",
        }


def test_an_uncommitted_artifact_still_gets_a_version_id(source_tree: Path, tmp_path: Path, monkeypatch):
    """source_tree is not a git repo, so `git log` yields nothing. Never fail over this."""
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    out, stage = tmp_path / "dist", tmp_path / "stage"

    build(source_tree, out, stage)

    assert all(a["version_id"] == "uncommitted" for a in _manifest_in(stage, "company")["artifacts"])


def test_github_sha_is_the_fallback_before_uncommitted(source_tree: Path, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GITHUB_SHA", "deadbeef")
    out, stage = tmp_path / "dist", tmp_path / "stage"

    build(source_tree, out, stage)

    assert all(a["version_id"] == "deadbeef" for a in _manifest_in(stage, "company")["artifacts"])


def test_the_optional_class_field_is_carried_through(source_tree: Path, tmp_path: Path):
    out, stage = tmp_path / "dist", tmp_path / "stage"
    build(source_tree, out, stage)

    rows = {a["id"]: a for a in _manifest_in(stage, "company")["artifacts"]}

    assert rows["discovery-workshop"]["class"] == "functional"
    assert rows["expense-policy"]["class"] is None


def test_a_status_header_is_lifted_into_the_manifest(source_tree: Path, tmp_path: Path):
    """This is what lets `get_domain_manifest` answer "which projects are at risk".

    Derived at package time rather than duplicated into artifact.yaml, for the same
    reason version_id is derived: two copies of a fact drift.
    """
    workshop = source_tree / "domains" / "company" / "discovery-workshop"
    (workshop / "status.md").write_text(
        "**As of 2026-08-28.**\n**Stage:** delivery.\n**Health:** at risk.\n",
        encoding="utf-8",
    )
    out, stage = tmp_path / "dist", tmp_path / "stage"

    build(source_tree, out, stage)
    rows = {a["id"]: a for a in _manifest_in(stage, "company")["artifacts"]}

    assert rows["discovery-workshop"]["progress"] == {
        "as_of": "2026-08-28",
        "stage": "delivery",
        "health": "at risk",
    }
    # No status.md, so no dead key. Presence answers "does this report progress".
    assert "progress" not in rows["expense-policy"]


def test_the_packaged_output_is_exactly_what_the_read_path_serves(tmp_path: Path, monkeypatch):
    """End to end over this repo's real content: package it, then walk it the way an
    agent does, discovering every id rather than knowing one. Deliberately names no
    domain or artifact, so renaming content cannot break it."""
    from server import artifacts

    out, stage = tmp_path / "dist", tmp_path / "stage"
    build(REPO_ROOT, out, stage)
    monkeypatch.setattr(artifacts, "ARTIFACTS_ROOT", stage)

    domains = artifacts.list_domains_payload()["domains"]
    assert domains, "this repo ships no domains"

    for domain in domains:
        manifest = artifacts.domain_manifest_payload(domain["id"])
        assert len(manifest["artifacts"]) == domain["artifact_count"]

        ids = [a["id"] for a in manifest["artifacts"]]
        fetched = artifacts.get_artifact_payload(domain["id"], ids)
        for entry in fetched["artifacts"]:
            assert entry["status"] == "found", entry
            readme = next(f for f in entry["files"] if f["path"] == "README.md")
            assert readme["content"].startswith("#")

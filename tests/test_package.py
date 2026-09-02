"""Packaging: the archive, the per-domain manifest, and the version stamp."""

from __future__ import annotations

import json
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.package_context import build  # noqa: E402
from server.access import POLICY_FILENAME  # noqa: E402
from tests.conftest import AS_COLLEAGUE  # noqa: E402

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


def test_the_archive_holds_the_domain_tree_and_the_access_policy_and_nothing_else(
    source_tree: Path, tmp_path: Path
):
    """The policy travels *with* the corpus, at the archive root beside `domains/`.

    It has to be in the archive, because the archive is the whole of what a deployment
    receives and the runtime reads the policy from the served root. It has to be beside
    `domains/` rather than inside it, because no read path can reach it there.
    """
    out, stage = tmp_path / "dist", tmp_path / "stage"
    build(source_tree, out, stage)

    with tarfile.open(out / "context.tar.gz") as tar:
        names = tar.getnames()

    assert "domains/company/expense-policy/README.md" in names
    assert "domains/company/_manifest.json" in names
    assert POLICY_FILENAME in names
    assert all(
        n in ("domains", POLICY_FILENAME) or n.startswith("domains/") for n in names
    ), names


def test_the_access_policy_is_staged_at_the_root_of_the_servable_tree(
    source_tree: Path, tmp_path: Path
):
    """dist/staging must be byte-identical to what extracting the archive produces, and
    it is what CONTEXT_ROOT points at in local dev."""
    out, stage = tmp_path / "dist", tmp_path / "stage"
    build(source_tree, out, stage)

    assert (stage / POLICY_FILENAME).is_file()
    assert "ctx.colleague" in (stage / POLICY_FILENAME).read_text(encoding="utf-8")


def test_the_upload_directory_still_holds_exactly_two_files(source_tree: Path, tmp_path: Path):
    """`az storage blob upload-batch --source dist/context` must never find a third.
    The policy goes into the archive, not beside it."""
    out, stage = tmp_path / "dist", tmp_path / "stage"
    build(source_tree, out, stage)

    assert sorted(p.name for p in out.iterdir()) == ["context.tar.gz", "manifest.json"]


def test_the_staged_policy_loads_as_a_policy(source_tree: Path, tmp_path: Path):
    """Staging the file is not enough; the runtime has to be able to read it from there."""
    from server import access

    out, stage = tmp_path / "dist", tmp_path / "stage"
    build(source_tree, out, stage)

    policy = access.load_policy(stage, mode=access.Mode.ENFORCE)
    assert policy.roles["ctx.colleague"]["company"] == "internal"


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
            "id", "title", "kind", "description", "review", "sensitivity", "class",
            "owner", "version_id",
        }


def test_a_manifest_row_carries_its_sensitivity_label(source_tree: Path, tmp_path: Path):
    """The runtime never reads artifact.yaml, only _manifest.json, so a label that does
    not reach the manifest does not exist as far as authorization is concerned."""
    out, stage = tmp_path / "dist", tmp_path / "stage"
    build(source_tree, out, stage)

    rows = {a["id"]: a for a in _manifest_in(stage, "company")["artifacts"]}
    assert rows["expense-policy"]["sensitivity"] == "internal"


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

    domains = artifacts.list_domains_payload(**AS_COLLEAGUE)["domains"]
    assert domains, "this repo ships no domains"

    for domain in domains:
        manifest = artifacts.domain_manifest_payload(domain["id"], **AS_COLLEAGUE)
        assert len(manifest["artifacts"]) == domain["artifact_count"]

        ids = [a["id"] for a in manifest["artifacts"]]
        fetched = artifacts.get_artifact_payload(domain["id"], ids, **AS_COLLEAGUE)
        for entry in fetched["artifacts"]:
            assert entry["status"] == "found", entry
            readme = next(f for f in entry["files"] if f["path"] == "README.md")
            assert readme["content"].startswith("#")

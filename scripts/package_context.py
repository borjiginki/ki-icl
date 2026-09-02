#!/usr/bin/env python3
"""Package domains/ into dist/context/{context.tar.gz, manifest.json}. `make package`.

Three things this does that the skills packager does not:

1. It stamps every artifact with a `version_id` taken from the last commit that
   touched that artifact's folder. Repository head would be wrong: it moves on every
   unrelated change and would report every artifact stale at once.
2. It emits a per-domain `_manifest.json` into the archive. The server serves
   `get_domain_manifest` straight out of that file, and it is the only place a
   version_id can be computed, because git is not available at runtime.
3. It carries `access-policy.yaml` to the root of both outputs, beside `domains/`. The
   grant table has to travel with the corpus it governs, or a deployment receives
   content whose read decisions it cannot see. Beside `domains/` and never inside it,
   because no read path can reach it there.

Two output directories, and the difference matters:

    dist/context/   the upload directory. Exactly two files, and `az storage blob
                    upload-batch --source dist/context` must never find more.
    dist/staging/   the servable tree, byte-identical to what extracting the archive
                    produces. This is what CONTEXT_ROOT points at in local dev.

dist/context/manifest.json beside the archive is informational only. The server never
reads it; change detection is the blob ETag.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

# Run directly (`python3 scripts/package_context.py`) and only scripts/ lands on
# sys.path, so the repo root has to be put there before `scripts.status_header` can be
# imported. The tests import this module as `scripts.package_context`, so a bare
# `import status_header` would work in one case and fail in the other.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import status_header  # noqa: E402
from server.access import POLICY_FILENAME  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "dist" / "context"
STAGE_DIR = ROOT / "dist" / "staging"

# `sensitivity` has to be here or authorization cannot see it: the runtime reads only
# `_manifest.json`, never `artifact.yaml`, so a label that stops at the source tree does
# not exist as far as the read path is concerned.
MANIFEST_FIELDS = ("title", "kind", "description", "review", "sensitivity", "class", "owner")


def _version_id(root: Path, rel_path: str) -> str:
    """Last commit touching `rel_path`, else GITHUB_SHA, else "uncommitted".

    Never fails packaging: an artifact added in the working tree but not yet
    committed is a normal state during review.
    """
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%H", "--", rel_path],
            cwd=root,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except OSError:
        out = ""
    return out or os.environ.get("GITHUB_SHA", "") or "uncommitted"


def _read_yaml(path: Path) -> dict:
    import yaml

    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _progress(artifact_dir: Path) -> dict | None:
    """The status header lifted out of `status.md`, or None for an artifact without one.

    Derived here rather than duplicated into artifact.yaml, for the same reason
    version_id is derived: two copies of a fact drift, and status.md is the one an
    engineer actually edits. This is what lets `get_domain_manifest` answer "which
    projects are at risk" without fetching every project in full.

    Deliberately not keyed on the domain. Any artifact carrying a status.md gets its
    header lifted, so a second domain that needs one needs no change here.
    """
    status = artifact_dir / "status.md"
    if not status.is_file():
        return None
    return status_header.parse(status.read_text(encoding="utf-8"))


def _domain_manifest(root: Path, domain_dir: Path) -> dict:
    domain = _read_yaml(domain_dir / "domain.yaml")
    artifacts = []
    for artifact_dir in sorted(d for d in domain_dir.iterdir() if d.is_dir()):
        meta = _read_yaml(artifact_dir / "artifact.yaml")
        row = {
            "id": artifact_dir.name,
            **{f: meta.get(f) for f in MANIFEST_FIELDS},
            "version_id": _version_id(
                root, f"domains/{domain_dir.name}/{artifact_dir.name}"
            ),
        }
        # Present only when there is one, so a presence check answers "does this
        # artifact report progress" and the other domains carry no dead keys.
        if (progress := _progress(artifact_dir)) is not None:
            row["progress"] = progress
        artifacts.append(row)
    return {
        "domain": domain_dir.name,
        "description": domain.get("description", ""),
        "owner": None,  # not used in phase 1; served so adding it later is data
        "artifacts": sorted(artifacts, key=lambda a: a["id"]),  # byte-stable across runs
    }


def build(root: Path = ROOT, out_dir: Path = OUT_DIR, stage_dir: Path = STAGE_DIR) -> dict:
    """Stage the servable tree, archive it, write both manifests. Returns the summary."""
    domain_dirs = sorted(d for d in (root / "domains").iterdir() if d.is_dir())

    # Rebuilt from scratch, so a removed artifact does not linger in a stale tree.
    shutil.rmtree(stage_dir, ignore_errors=True)
    manifests = []
    for domain_dir in domain_dirs:
        staged = stage_dir / "domains" / domain_dir.name
        shutil.copytree(domain_dir, staged)
        manifest = _domain_manifest(root, domain_dir)
        manifests.append(manifest)
        (staged / "_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    # The grant table travels with the corpus it governs, at the root beside `domains/`.
    # In the archive because the archive is the whole of what a deployment receives and
    # the runtime reads the policy from the served root; beside `domains/` rather than
    # inside it because no read path can reach it there. It is deliberately NOT a third
    # file in out_dir: `az storage blob upload-batch --source dist/context` must keep
    # finding exactly two.
    policy = root / POLICY_FILENAME
    shutil.copy2(policy, stage_dir / POLICY_FILENAME)

    out_dir.mkdir(parents=True, exist_ok=True)
    archive = out_dir / "context.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(stage_dir / "domains", arcname="domains")
        tar.add(stage_dir / POLICY_FILENAME, arcname=POLICY_FILENAME)

    summary = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "commit": os.environ.get("GITHUB_SHA", ""),
        "archive": "context.tar.gz",
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "domain_count": len(manifests),
        "artifact_count": sum(len(m["artifacts"]) for m in manifests),
    }
    (out_dir / "manifest.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


if __name__ == "__main__":
    print(json.dumps(build(), indent=2))

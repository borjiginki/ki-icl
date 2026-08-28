#!/usr/bin/env python3
"""Package domains/ into dist/context/{context.tar.gz, manifest.json}. `make package`.

Two things this does that the skills packager does not:

1. It stamps every artifact with a `version_id` taken from the last commit that
   touched that artifact's folder. Repository head would be wrong: it moves on every
   unrelated change and would report every artifact stale at once.
2. It emits a per-domain `_manifest.json` into the archive. The server serves
   `get_domain_manifest` straight out of that file, and it is the only place a
   version_id can be computed, because git is not available at runtime.

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
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "dist" / "context"
STAGE_DIR = ROOT / "dist" / "staging"

MANIFEST_FIELDS = ("title", "kind", "description", "class", "owner")


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


def _domain_manifest(root: Path, domain_dir: Path) -> dict:
    domain = _read_yaml(domain_dir / "domain.yaml")
    artifacts = []
    for artifact_dir in sorted(d for d in domain_dir.iterdir() if d.is_dir()):
        meta = _read_yaml(artifact_dir / "artifact.yaml")
        artifacts.append(
            {
                "id": artifact_dir.name,
                **{f: meta.get(f) for f in MANIFEST_FIELDS},
                "version_id": _version_id(
                    root, f"domains/{domain_dir.name}/{artifact_dir.name}"
                ),
            }
        )
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

    out_dir.mkdir(parents=True, exist_ok=True)
    archive = out_dir / "context.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(stage_dir / "domains", arcname="domains")

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

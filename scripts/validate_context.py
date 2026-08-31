#!/usr/bin/env python3
"""Structural validation gate for the context repo. `make validate`.

Collects EVERY failure and prints them all, because a contributor fixing one
thing at a time is the failure mode this avoids. Exits 1 if there are any.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

# Run directly (`python3 scripts/validate_context.py`) and only scripts/ lands on
# sys.path, so the repo root has to be put there before `scripts.status_header` can be
# imported. The tests import this module as `scripts.validate_context`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import status_header  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# Text only, by construction. Repository size risk is entirely a binaries risk:
# measured on ki-dev-skills, 147 markdown files are 1.73 MB and the binaries beside
# them are 15.32 MB. Widening this is a deliberate decision, not a convenience.
ALLOWED_SUFFIXES = {".md", ".yaml", ".yml", ".json", ".txt", ".csv"}
MAX_FILE_BYTES = 1_048_576

# The partition is a decision, not a convention: issue #20 OQ-13, settled 2026-08-31
# on the value-chain-plus-support-function model, with `company` added for facts that
# belong to no single function and `projects` for per-engagement state. A new domain
# changes how the whole corpus is organised and every telemetry key written against it,
# so it belongs in a pull request that says so rather than in a mkdir.
KNOWN_DOMAINS = {
    "company",
    "finance",
    "hr",
    "marketing",
    "projects",
    "sales",
    "value-creation",
    "value-delivery",
}

# A domain whose artifacts all answer the same questions is only usable if they answer
# them in the same place. A project whose stage is buried in README.md prose cannot
# answer "what stage is it at", and an agent cannot learn one layout per project.
# Kept as data rather than a per-domain branch so the next domain that needs a shape is
# a dictionary entry. See domains/value-creation/project-status-reporting/.
REQUIRED_ARTIFACT_FILES: dict[str, tuple[str, ...]] = {
    "projects": ("status.md", "team.md"),
}
ID_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
REQUIRED_ARTIFACT_FIELDS = ("title", "kind", "description")

# `status.md` carries a header the packager lifts into the manifest, so it is gated
# here against the same definitions the packager derives from. Two copies of the
# format would drift, and a header the gate accepts but the packager cannot read
# would reach the manifest as a silent null.
STATUS_FILE = "status.md"


def _load_yaml(path: Path, errors: list[str]) -> dict | None:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        errors.append(f"{path}: unreadable YAML ({exc})")
        return None
    if not isinstance(data, dict):
        errors.append(f"{path}: expected a YAML mapping")
        return None
    return data


def _non_empty(data: dict, field: str, path: Path, errors: list[str]) -> None:
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{path}: `{field}` is required and must be a non-empty string")


def validate(root: Path) -> list[str]:
    """Return every structural error found under `<root>/domains/`. Empty means valid."""
    errors: list[str] = []
    domains_dir = root / "domains"
    if not domains_dir.is_dir():
        return [f"{domains_dir}: no domains/ directory"]

    for domain_dir in sorted(d for d in domains_dir.iterdir() if d.is_dir()):
        _validate_domain(domain_dir, errors)

    for file in sorted(p for p in domains_dir.rglob("*") if p.is_file()):
        rel = file.relative_to(root)
        if file.suffix.lower() not in ALLOWED_SUFFIXES:
            errors.append(
                f"{rel}: `{file.suffix}` is not an allowed extension. This phase is "
                f"text only ({', '.join(sorted(ALLOWED_SUFFIXES))}); binaries such as "
                f"images, PDFs and Office documents are out of scope."
            )
        if file.stat().st_size > MAX_FILE_BYTES:
            errors.append(f"{rel}: {file.stat().st_size} bytes exceeds the {MAX_FILE_BYTES} byte cap")

    return errors


def _validate_domain(domain_dir: Path, errors: list[str]) -> None:
    if domain_dir.name not in KNOWN_DOMAINS:
        errors.append(
            f"{domain_dir.name}/: not one of the agreed domains "
            f"({', '.join(sorted(KNOWN_DOMAINS))}). The partition is a decision, so "
            f"adding a domain means amending KNOWN_DOMAINS in this file and saying why "
            f"in the pull request. If this folder is meant to be an artifact, it belongs "
            f"one level down, inside the domain that will own it."
        )

    domain_yaml = domain_dir / "domain.yaml"
    if not domain_yaml.is_file():
        errors.append(f"{domain_dir.name}/: missing domain.yaml")
    elif (data := _load_yaml(domain_yaml, errors)) is not None:
        if data.get("id") != domain_dir.name:
            errors.append(
                f"{domain_yaml.parent.name}/domain.yaml: `id` is "
                f"{data.get('id')!r} but the folder is {domain_dir.name!r}"
            )
        _non_empty(data, "description", domain_yaml, errors)

    seen: set[str] = set()
    for artifact_dir in sorted(d for d in domain_dir.iterdir() if d.is_dir()):
        _validate_artifact(artifact_dir, seen, errors)


def _validate_artifact(artifact_dir: Path, seen: set[str], errors: list[str]) -> None:
    label = f"{artifact_dir.parent.name}/{artifact_dir.name}"

    if not ID_PATTERN.match(artifact_dir.name):
        errors.append(f"{label}: folder name must be lowercase kebab-case")
    if artifact_dir.name in seen:
        errors.append(f"{label}: duplicate artifact id within the domain")
    seen.add(artifact_dir.name)

    artifact_yaml = artifact_dir / "artifact.yaml"
    if not artifact_yaml.is_file():
        # A directory under a domain with no artifact.yaml is a half-finished removal.
        errors.append(f"{label}/: missing artifact.yaml (every folder under a domain is an artifact)")
    elif (data := _load_yaml(artifact_yaml, errors)) is not None:
        for field in REQUIRED_ARTIFACT_FIELDS:
            _non_empty(data, field, artifact_yaml, errors)

    if not (artifact_dir / "README.md").is_file():
        errors.append(f"{label}/: missing README.md (the entry document is required)")

    for name in REQUIRED_ARTIFACT_FILES.get(artifact_dir.parent.name, ()):
        path = artifact_dir / name
        if not path.is_file():
            errors.append(
                f"{label}/: missing {name}, which every artifact in "
                f"`{artifact_dir.parent.name}/` must have so the same question is "
                f"answered from the same place in every one"
            )
            continue
        header = status_header.parse(path.read_text(encoding="utf-8"))
        if header["as_of"] is None:
            errors.append(
                f"{label}/{name}: no `**As of YYYY-MM-DD**` line. Without it a stale "
                f"file answers confidently, because `version_id` is opaque and cannot "
                f"carry recency."
            )
        if name == STATUS_FILE:
            _status_vocabulary(label, header, errors)


def _status_vocabulary(label: str, header: dict, errors: list[str]) -> None:
    """Stage and health must come from the closed lists, or comparing projects fails.

    The packager lifts both into the manifest so that `get_domain_manifest` can answer
    "which projects are at risk" in one call. Free text defeats that: `in progress`,
    `ongoing` and `phase 2` are all answers somebody would write, and none of them
    compares to anything.
    """
    for field, allowed in (
        ("stage", status_header.STAGES),
        ("health", status_header.HEALTHS),
    ):
        if header[field] is None:
            errors.append(
                f"{label}/{STATUS_FILE}: needs a line `**{field.title()}:** <value>` "
                f"with one of: {', '.join(allowed)}. Anything else is dropped rather "
                f"than guessed, and the manifest would carry a silent null."
            )


def main() -> int:
    errors = validate(ROOT)
    if errors:
        print(f"{len(errors)} validation error(s):\n", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    domains = sorted(d for d in (ROOT / "domains").iterdir() if d.is_dir())
    artifacts = [a for d in domains for a in d.iterdir() if a.is_dir()]
    total = sum(f.stat().st_size for f in (ROOT / "domains").rglob("*") if f.is_file())
    print(f"OK: {len(domains)} domain(s), {len(artifacts)} artifact(s), {total} bytes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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

ROOT = Path(__file__).resolve().parent.parent

# Text only, by construction. Repository size risk is entirely a binaries risk:
# measured on ki-dev-skills, 147 markdown files are 1.73 MB and the binaries beside
# them are 15.32 MB. Widening this is a deliberate decision, not a convenience.
ALLOWED_SUFFIXES = {".md", ".yaml", ".yml", ".json", ".txt", ".csv"}
MAX_FILE_BYTES = 1_048_576
ID_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
REQUIRED_ARTIFACT_FIELDS = ("title", "kind", "description")


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

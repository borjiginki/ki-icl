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

# The ladder is defined in the runtime module rather than here, and imported, because
# the gate must accept exactly what the server compares against. Two copies would
# drift, which is the problem `status_header` exists to solve, one layer down. This
# inverts the usual scripts/ -> server/ direction and is acceptable only because
# `server.access` imports no FastMCP and reads no environment.
from server.access import POLICY_FILENAME, SENSITIVITY_LEVELS  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# Text only, by construction. Repository size risk is entirely a binaries risk:
# measured on ki-dev-skills, 147 markdown files are 1.73 MB and the binaries beside
# them are 15.32 MB. Widening this is a deliberate decision, not a convenience.
ALLOWED_SUFFIXES = {".md", ".yaml", ".yml", ".json", ".txt", ".csv"}
MAX_FILE_BYTES = 1_048_576

# The partition is a decision, not a convention: revised 2026-09-09 when real content
# replaced the placeholder value-chain-plus-support-function model. The original eight
# business-function domains assumed a shape the real corpus did not have: several stood
# empty with no content in sight, while two real, differently-shaped bodies of material
# (a 119-case reference library and 13 personal staffing profiles) had nowhere to go
# without either overloading `sales`/`hr` or fragmenting across them. The domains below
# instead follow what an agent is actually trying to do, mirroring the organizing
# principle of the source layer this content was drawn from. `company` and `projects`
# keep their original justification: identity facts belong to no single function, and
# `projects` is per-engagement state rather than stable, reusable material. A new domain
# changes how the whole corpus is organised and every telemetry key written against it,
# so it belongs in a pull request that says so rather than in a mkdir.
KNOWN_DOMAINS = {
    "company",
    "method",
    "offerings",
    "case-studies",
    "team",
    "marketing",
    "projects",
}

# A domain whose artifacts all answer the same questions is only usable if they answer
# them in the same place. A project whose stage is buried in README.md prose cannot
# answer "what stage is it at", and an agent cannot learn one layout per project.
# Kept as data rather than a per-domain branch so the next domain that needs a shape is
# a dictionary entry. See domains/method/project-status-reporting/.
REQUIRED_ARTIFACT_FILES: dict[str, tuple[str, ...]] = {
    "projects": ("status.md", "team.md"),
}
ID_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
REQUIRED_ARTIFACT_FIELDS = ("title", "kind", "description", "review", "sensitivity")

# Entra app-role values. Dotted rather than kebab-case so they read as role names
# rather than as artifact ids, and constrained at all so a grant cannot be keyed on a
# display name that an administrator will later rename.
ROLE_PATTERN = re.compile(r"^[a-z][a-z0-9.\-]*$")

# Required, not optional, and this is the reason: the README has always said the
# content here is unreviewed placeholder text, and no caller could ever see that. A
# manifest row reads as settled fact whatever its provenance, and the row is now the
# whole answer to a portfolio question, so an unreviewed document gets repeated as
# true with nothing anywhere to contradict it. Serving the field makes that
# impossible to do by accident; leaving it optional would make it possible again for
# every artifact that omitted it.
REVIEW_STATES = {
    "demo": "invented content that exists to exercise the pipeline",
    "draft": "real subject, written but not reviewed by an owner",
    "approved": "an owner has reviewed it and stands behind it",
}

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

    _validate_policy(root, errors)

    for file in sorted(p for p in domains_dir.rglob("*") if p.is_file()):
        rel = file.relative_to(root)
        if file.name == POLICY_FILENAME:
            errors.append(
                f"{rel}: the access policy must live at the root of the tree, never "
                f"under domains/. Beside `domains/` no read path can reach it; inside "
                f"one it is an artifact-adjacent file, and the grant table is the last "
                f"thing that should be servable."
            )
        if file.suffix.lower() not in ALLOWED_SUFFIXES:
            errors.append(
                f"{rel}: `{file.suffix}` is not an allowed extension. This phase is "
                f"text only ({', '.join(sorted(ALLOWED_SUFFIXES))}); binaries such as "
                f"images, PDFs and Office documents are out of scope."
            )
        if file.stat().st_size > MAX_FILE_BYTES:
            errors.append(f"{rel}: {file.stat().st_size} bytes exceeds the {MAX_FILE_BYTES} byte cap")

    return errors


def _validate_policy(root: Path, errors: list[str]) -> None:
    """Gate `access-policy.yaml`. A corpus nobody has decided the access for is invalid.

    The load-bearing rule is the last one: the domain keys across the whole file must
    equal KNOWN_DOMAINS *exactly*, in both directions. One rule catches a typo'd grant
    that silently grants nothing, and a newly added domain that nobody has decided the
    access for. It is what makes a new domain undeployable rather than merely invisible,
    matching the "a decision, not a convention" rule the partition already lives by.

    The runtime loader is deliberately more forgiving than this: it drops a grant it
    cannot understand and keeps serving, because a policy that breaks after deployment
    must degrade to denial rather than to an exception. This gate is where the
    narrowing is refused outright, so that never reaches a deployment in the first
    place.
    """
    path = root / POLICY_FILENAME
    if not path.is_file():
        errors.append(
            f"{POLICY_FILENAME}: missing. Every domain needs a read decision before the "
            f"corpus can be served, and this file is where they live."
        )
        return

    data = _load_yaml(path, errors)
    if data is None:
        return

    roles = data.get("roles")
    if not isinstance(roles, dict) or not roles:
        errors.append(f"{POLICY_FILENAME}: `roles` is required and must be a non-empty mapping")
        return

    granted: set[str] = set()
    for role, body in roles.items():
        if not isinstance(role, str) or not ROLE_PATTERN.match(role):
            errors.append(
                f"{POLICY_FILENAME}: role name {role!r} must be a lowercase dotted slug, "
                f"for example `ctx.delivery`. It is an Entra app-role value, so it has to "
                f"survive being typed into an app registration."
            )
        if not isinstance(body, dict):
            errors.append(f"{POLICY_FILENAME}: role {role!r} must be a mapping")
            continue

        value = body.get("description")
        if not isinstance(value, str) or not value.strip():
            errors.append(
                f"{POLICY_FILENAME}: role {role!r} needs a non-empty `description`. It is "
                f"what makes this file reviewable by somebody who does not read Python, "
                f"which is the reason grants live here rather than only in Entra."
            )

        grants = body.get("grants")
        if grants is None:
            grants = {}
        if not isinstance(grants, dict):
            errors.append(f"{POLICY_FILENAME}: role {role!r} has a `grants` that is not a mapping")
            continue

        for domain, level in grants.items():
            granted.add(str(domain))
            if level not in SENSITIVITY_LEVELS:
                errors.append(
                    f"{POLICY_FILENAME}: role {role!r} grants `{domain}` at {level!r}, which "
                    f"is not one of "
                    + ", ".join(f"`{k}` ({v})" for k, v in SENSITIVITY_LEVELS.items())
                )

    for domain in sorted(KNOWN_DOMAINS - granted):
        errors.append(
            f"{POLICY_FILENAME}: `{domain}` is a known domain that no role grants, so "
            f"nobody could read it. Every domain needs a read decision; grant it to a "
            f"role, or remove it from KNOWN_DOMAINS."
        )
    for domain in sorted(granted - KNOWN_DOMAINS):
        errors.append(
            f"{POLICY_FILENAME}: `{domain}` is granted but is not one of the agreed "
            f"domains ({', '.join(sorted(KNOWN_DOMAINS))}). A grant for a domain that "
            f"does not exist grants nothing today and something unintended the day "
            f"somebody creates that folder."
        )


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
        if (review := data.get("review")) is not None and review not in REVIEW_STATES:
            errors.append(
                f"{label}/artifact.yaml: `review` is {review!r}, which is not one of "
                + ", ".join(f"`{k}` ({v})" for k, v in REVIEW_STATES.items())
            )
        if (level := data.get("sensitivity")) is not None and level not in SENSITIVITY_LEVELS:
            errors.append(
                f"{label}/artifact.yaml: `sensitivity` is {level!r}, which is not one of "
                + ", ".join(f"`{k}` ({v})" for k, v in SENSITIVITY_LEVELS.items())
            )

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

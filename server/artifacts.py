"""The context-layer read path: discovery, lookup, and payload construction.

Serves a *packaged* catalog tree, the one `scripts/package_context.py` produces:

    <root>/domains/<domain>/_manifest.json
    <root>/domains/<domain>/<artifact-id>/...

Nothing here imports FastMCP, so it is testable without a server.

Lifting this into ki-mcp as `server/utils/artifacts.py`: replace ARTIFACTS_ROOT
with `settings.artifacts_dir` and `_file_payload` with `utils.file_payload.file_payload`.
Everything else moves unchanged.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# The *path* is bound once; the directory *contents* are read on every call, so a
# background refresh becomes visible without a restart.
ARTIFACTS_ROOT: Path = Path(os.environ.get("CONTEXT_ROOT", "dist/context")).resolve()

_TEXT_MIME = {
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".json": "text/json",
    ".yaml": "text/yaml",
    ".yml": "text/yaml",
    ".csv": "text/csv",
}

_LIST_HINT = (
    "Read `get_domain_manifest(domain)` to list a domain's artifacts, then "
    "`get_artifact(domain, ids)` to fetch them."
)
_FETCH_HINT = (
    "Text files use encoding=utf-8. A `not_found` entry means no artifact has that "
    "id; do not substitute a similar one."
)


# --- discovery --------------------------------------------------------------


def iter_domains() -> list[tuple[str, Path]]:
    """(domain_id, domain_dir) for every directory under `<root>/domains/`, sorted."""
    domains_dir = ARTIFACTS_ROOT / "domains"
    if not domains_dir.is_dir():
        return []
    return sorted((d.name, d) for d in domains_dir.iterdir() if d.is_dir())


def visible_domains() -> list[tuple[str, Path]]:
    """The scoping seam. Phase 1 returns iter_domains() unchanged.

    Every read path resolves domains through this function and nothing else, so that
    when per-identity scoping lands there is exactly one place to change and no read
    path can be forgotten. Do not bypass it, even though it is currently a
    pass-through.
    """
    return iter_domains()


def domain_manifest(domain_dir: Path) -> dict[str, Any] | None:
    """Parse `<domain_dir>/_manifest.json`. None when absent or unparseable."""
    path = domain_dir / "_manifest.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("skipping domain %s: unreadable _manifest.json (%s)", domain_dir.name, exc)
        return None


def _described_domains() -> list[tuple[str, Path, dict[str, Any]]]:
    """Visible domains that carry a readable manifest. A half-described one is skipped."""
    out = []
    for name, path in visible_domains():
        manifest = domain_manifest(path)
        if manifest is not None:
            out.append((name, path, manifest))
    return out


def _not_found_domain(domain: str) -> dict[str, Any]:
    """`known` lets a caller that mistyped recover in one step. Not a similarity hint."""
    return {
        "status": "not_found",
        "domain": domain,
        "known": [name for name, _, _ in _described_domains()],
    }


# --- payloads ---------------------------------------------------------------


def list_domains_payload() -> dict[str, Any]:
    """The cold-start entry point. One row per domain, forever."""
    return {
        "domains": [
            {
                "id": name,
                "description": manifest.get("description", ""),
                # Always "governed" / null in phase 1; the fields exist so that adding
                # mapped sources and ownership later is data, not a schema change.
                "kind": "governed",
                "owner": manifest.get("owner"),
                "artifact_count": len(manifest.get("artifacts", [])),
            }
            for name, _, manifest in _described_domains()
        ],
        "fetch_hint": _LIST_HINT,
    }


def domain_manifest_payload(domain: str) -> dict[str, Any]:
    """One domain's metadata plus one row per artifact. No file bodies."""
    for name, _, manifest in _described_domains():
        if name == domain:
            rows = manifest.get("artifacts", [])
            return {
                "domain": name,
                "description": manifest.get("description", ""),
                "owner": manifest.get("owner"),
                "artifacts": rows,
                # A domain with nothing in it yet is a normal state, and telling an
                # agent to fetch from it would be a dead end. The gap is the only
                # useful thing it can do here, and the only way this domain learns.
                "fetch_hint": (
                    f'Fetch with `get_artifact("{name}", ["<id>"])`.'
                    if rows
                    else (
                        f"Nothing is published in `{name}` yet. Tell the user it is not "
                        f'available, and record the need with `report_gap("{name}", '
                        f'"<topic>")`. Do not answer from another domain.'
                    )
                ),
            }
    return _not_found_domain(domain)


def get_artifact_payload(
    domain: str, ids: str | list[str], *, max_file_bytes: int = 1_048_576
) -> dict[str, Any]:
    """Full text of one or more artifacts. An exact hit or an honest miss, never both."""
    if isinstance(ids, str):
        ids = [ids]

    for name, domain_dir, manifest in _described_domains():
        if name == domain:
            rows = {row["id"]: row for row in manifest.get("artifacts", [])}
            return {
                "domain": name,
                "artifacts": [
                    _artifact_entry(domain_dir, rows, aid, max_file_bytes) for aid in ids
                ],
                "fetch_hint": _FETCH_HINT,
            }
    return _not_found_domain(domain)


def _artifact_entry(
    domain_dir: Path, rows: dict[str, dict], artifact_id: str, max_file_bytes: int
) -> dict[str, Any]:
    """One batch entry. A miss degrades this item only; the call still succeeds."""
    row = rows.get(artifact_id)  # exact dict hit; there is no fallback by design
    path = _safe_artifact_dir(domain_dir, artifact_id)
    if row is None or path is None:
        return {"status": "not_found", "id": artifact_id}

    files, skipped = [], []
    for f in sorted(p for p in path.rglob("*") if p.is_file()):
        rel = f.relative_to(path).as_posix()
        payload = _file_payload(f, rel_path=rel, max_bytes=max_file_bytes)
        (skipped if payload.get("skipped") else files).append(
            rel if payload.get("skipped") else payload
        )

    return {
        "status": "found",
        **{k: row.get(k) for k in ("id", "title", "kind", "description", "class", "owner")},
        "version_id": row.get("version_id"),
        "file_count": len(files),
        "files": files,
        "skipped_files": skipped,
    }


def _safe_artifact_dir(domain_dir: Path, artifact_id: str) -> Path | None:
    """Resolve `artifact_id` under `domain_dir`, or None if it escapes or is absent.

    The id is caller-supplied, so `../` must be impossible.
    """
    try:
        path = (domain_dir / artifact_id).resolve()
        path.relative_to(domain_dir.resolve())
    except (ValueError, OSError):
        return None
    return path if path.is_dir() else None


def _file_payload(path: Path, *, rel_path: str, max_bytes: int) -> dict[str, Any]:
    """One file entry: utf-8 `content`, or a `skipped` marker when oversized.

    Trimmed copy of ki-mcp's `utils/file_payload.file_payload`, kept identical in
    shape so this module can call the real one once it lands there.
    """
    size = path.stat().st_size
    mime_type = _TEXT_MIME.get(path.suffix.lower(), "application/octet-stream")
    base = {"path": rel_path, "mime_type": mime_type, "size": size}

    if size > max_bytes:
        return {**base, "skipped": True, "reason": f"exceeds max_file_bytes ({size} > {max_bytes})"}

    if mime_type.startswith("text/"):
        try:
            return {**base, "encoding": "utf-8", "content": path.read_text(encoding="utf-8")}
        except UnicodeDecodeError:
            pass
    return {
        **base,
        "encoding": "base64",
        "content": base64.b64encode(path.read_bytes()).decode("ascii"),
    }

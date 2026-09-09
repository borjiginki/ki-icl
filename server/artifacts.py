"""The context-layer read path: discovery, lookup, and payload construction.

Serves a *packaged* catalog tree, the one ki-ccl's `scripts/package_context.py` produces:

    <root>/domains/<domain>/_manifest.json
    <root>/domains/<domain>/<artifact-id>/...
    <root>/access-policy.yaml          (never reachable by any read path here)

Nothing here imports FastMCP, so it is testable without a server. It imports
`server.access` for the authorization predicates, which is safe for the same reason:
that module imports no FastMCP and reads no environment either.

## Two seams, one per axis

Every read resolves domains through `visible_domains()` and rows through
`_readable_domains()`, and nothing else. Filtering happens *there* rather than in the
payload builders, and that placement is the whole design: it means

  * `artifact_count` counts only readable rows,
  * `_review_caveat` cannot name a denied id inside `fetch_hint`,
  * `rows.get(artifact_id)` returns None for a denied id, so the existing honest-miss
    path serves it byte-identically,
  * `_not_found_domain`'s `known` list is filtered,

all without any of those four functions knowing that authorization exists. The
load-bearing property is that after redaction the unfiltered row list is never bound to
a local variable again: `manifest.get("artifacts", [])` appears exactly ONCE in this
module, pinned by `test_the_raw_artifact_list_is_read_in_exactly_one_place`.

`principal` and `policy` are required keyword-only arguments on every payload function,
pinned by `test_every_payload_function_requires_a_principal`. Not defaulted, because a
default makes "forgot the principal" indistinguishable from "passed the right one" at
the call site, which is the failure the seam exists to prevent. Not a contextvar,
because a forgotten read path would silently inherit whoever is current, turning an
availability bug into a disclosure one.

## Invariants for whoever changes this next

**The unit of access is the artifact folder.** `_artifact_entry` returns every file
under it via `rglob`, `artifact.yaml` included. There are deliberately no per-file
rules, so a restricted annex must not be placed inside a readable artifact.

**When the blob cache lands, the cache holds RAW manifests and filtering happens after
the cache, per request, always.** A cached *filtered* manifest served to a second
principal is a cross-principal disclosure. This is invisible today only because
`domain_manifest()` re-reads from disk on every call.

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

from server import access
from server.access import Policy, Principal

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


def visible_domains(*, principal: Principal, policy: Policy) -> list[tuple[str, Path]]:
    """The domain seam: the domains on disk this caller may read, sorted.

    Every read path resolves domains through this function and nothing else, so there is
    exactly one place a domain decision is made and no read path can be written that
    forgets it. `test_iter_domains_is_reached_only_through_the_visible_domains_seam`
    makes that mechanical rather than a promise. Do not bypass it.
    """
    kept, denied = [], []
    for name, path in iter_domains():
        target = kept if access.may_read_domain(policy, principal, name) else denied
        target.append((name, path))
    # Routed through `enforce` rather than filtered here, so observe mode covers BOTH
    # axes. Hard-denying domains while un-filtering rows would make the dry run cover
    # half the model, and the half it hid is the one that changes what a caller can see
    # at all.
    return access.enforce(policy, kept, denied, key=lambda pair: pair[0])


def domain_manifest(domain_dir: Path) -> dict[str, Any] | None:
    """Parse `<domain_dir>/_manifest.json`. None when absent or unparseable.

    Re-read on every call, and that is what makes redaction downstream safe. When a
    cache lands here it must hold the RAW manifest: caching a *filtered* one would serve
    one caller's view to the next, which is the worst failure this module can have and
    the least visible.
    """
    path = domain_dir / "_manifest.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("skipping domain %s: unreadable _manifest.json (%s)", domain_dir.name, exc)
        return None


def _readable_domains(
    *, principal: Principal, policy: Policy
) -> list[tuple[str, Path, dict[str, Any]]]:
    """The row seam: readable domains, each carrying an ALREADY-REDACTED manifest.

    Two jobs in one function, describe and redact, deliberately. Redacting here is what
    lets every payload builder downstream stay correct without knowing authorization
    exists, because none of them ever sees an unfiltered row. A half-described domain
    (no readable manifest) is skipped, as before.
    """
    out = []
    for name, path in visible_domains(principal=principal, policy=policy):
        manifest = domain_manifest(path)
        if manifest is None:
            continue
        kept, denied = access.partition_rows(
            policy, principal, name, manifest.get("artifacts", [])
        )
        rows = access.enforce(policy, kept, denied, key=lambda row: row.get("id", ""))
        out.append((name, path, {**manifest, "artifacts": rows}))
    return out


def _unserved_domain(domain: str, *, principal: Principal, policy: Policy) -> dict[str, Any]:
    """The answer for a domain this caller did not get: `forbidden` or `not_found`.

    The asymmetry with denied *rows* is deliberate. A domain the policy knows about but
    has not granted answers `forbidden`, honestly, because the domain names are KI
    group's business functions and are disclosed to any authenticated caller by design
    (the invariant is written down in access-policy.yaml). `not_found` there would be a
    lie, would have the agent tell the user the domain does not exist, and would
    manufacture a false gap for a document that exists.

    Artifact ids get the opposite treatment, because they are customer names: a denied
    row is simply absent and a denied fetch is an honest-looking miss, so ids cannot be
    enumerated.

    A domain that is granted but simply absent from the tree stays `not_found`. If
    `forbidden` became the answer to everything, the miss table would stop meaning what
    it means.
    """
    if domain in policy.roles_domains() and not access.may_read_domain(
        policy, principal, domain
    ):
        return {
            "status": "forbidden",
            "domain": domain,
            "fetch_hint": (
                f"`{domain}` exists but this user may not read it. Tell them they do not "
                f"have access. Do NOT call `report_gap` for it: nothing is missing. Do "
                f"not guess at what it contains, and do not answer from another domain."
            ),
        }
    return _not_found_domain(domain, principal=principal, policy=policy)


def _not_found_domain(domain: str, *, principal: Principal, policy: Policy) -> dict[str, Any]:
    """`known` lets a caller that mistyped recover in one step. Not a similarity hint.

    Filtered to readable domains. That is a usability decision rather than a security
    boundary, since `forbidden` already discloses the domain universe on purpose, but a
    mistyped id should not be a cheaper way to enumerate than the honest answer.
    """
    return {
        "status": "not_found",
        "domain": domain,
        "known": [
            name for name, _ in visible_domains(principal=principal, policy=policy)
        ],
    }


# --- payloads ---------------------------------------------------------------


def list_domains_payload(*, principal: Principal, policy: Policy) -> dict[str, Any]:
    """The cold-start entry point. One row per domain this caller may read.

    The listing differs between callers, which is why the tool description tells an
    agent not to reuse one from earlier in the conversation. `artifact_count` is
    correct for free, because the manifest it counts has already been redacted.
    """
    return {
        "domains": [
            {
                "id": name,
                "description": manifest.get("description", ""),
                # Always "governed" / null in phase 1; the fields exist so that adding
                # mapped sources and ownership later is data, not a schema change.
                "kind": "governed",
                "owner": manifest.get("owner"),
                "artifact_count": len(manifest["artifacts"]),
            }
            for name, _, manifest in _readable_domains(principal=principal, policy=policy)
        ],
        "fetch_hint": _LIST_HINT,
    }


def _review_caveat(rows: list[dict[str, Any]]) -> str:
    """A warning naming the unapproved rows, or "" when every row is approved.

    Attached to the manifest rather than left to the instructions because this is the
    payload an agent answers a cross-artifact question from. A row reads as settled
    fact whatever its provenance, and the caveat has to travel with the row that
    needs it: an agent that never opens the files never sees the "demo content"
    banner inside them, which is exactly how invented data gets repeated as true.
    """
    unapproved = sorted(
        {row.get("review") for row in rows if row.get("review") not in (None, "approved")}
    )
    if not unapproved:
        return ""
    ids = {
        state: [r["id"] for r in rows if r.get("review") == state] for state in unapproved
    }
    return " NOT APPROVED: " + "; ".join(
        f"`{state}` ({', '.join(sorted(found))})" for state, found in ids.items()
    ) + ". Say so in any answer drawn from these, and never present `demo` content as fact."


def domain_manifest_payload(
    domain: str, *, principal: Principal, policy: Policy
) -> dict[str, Any]:
    """One domain's metadata plus one row per readable artifact. No file bodies."""
    for name, _, manifest in _readable_domains(principal=principal, policy=policy):
        if name == domain:
            rows = manifest["artifacts"]
            return {
                "domain": name,
                "description": manifest.get("description", ""),
                "owner": manifest.get("owner"),
                "artifacts": rows,
                # A domain with nothing readable in it is a normal state, and telling an
                # agent to fetch from it would be a dead end. The gap is the only useful
                # thing it can do here, and the only way this domain learns.
                #
                # The wording is caller-relative on purpose. A genuinely empty domain and
                # one whose every row was filtered must render IDENTICALLY, or the
                # difference is an enumeration oracle: walk the domains and learn which
                # hold something hidden. "Nothing is published yet" would be false in the
                # filtered case; "nothing available to you" is true in both.
                "fetch_hint": (
                    f'Fetch with `get_artifact("{name}", ["<id>"])`.'
                    + _review_caveat(rows)
                    if rows
                    else (
                        f"Nothing in `{name}` is available to you. Tell the user it is "
                        f'not available, and record the need with `report_gap("{name}", '
                        f'"<topic>")`. Do not answer from another domain.'
                    )
                ),
            }
    return _unserved_domain(domain, principal=principal, policy=policy)


def get_artifact_payload(
    domain: str,
    ids: str | list[str],
    *,
    principal: Principal,
    policy: Policy,
    max_file_bytes: int = 1_048_576,
) -> dict[str, Any]:
    """Full text of one or more artifacts. An exact hit or an honest miss, never both.

    A denied id needs no branch of its own: the manifest arrived redacted, so `rows` has
    no entry for it and `_artifact_entry` takes the existing not_found path. That is why
    a denial is byte-identical to a genuine miss, and why no file belonging to a denied
    artifact is ever opened.
    """
    if isinstance(ids, str):
        ids = [ids]

    for name, domain_dir, manifest in _readable_domains(principal=principal, policy=policy):
        if name == domain:
            rows = {row["id"]: row for row in manifest["artifacts"]}
            return {
                "domain": name,
                "artifacts": [
                    _artifact_entry(domain_dir, rows, aid, max_file_bytes) for aid in ids
                ],
                "fetch_hint": _FETCH_HINT,
            }
    return _unserved_domain(domain, principal=principal, policy=policy)


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
        **{k: row.get(k) for k in ("id", "title", "kind", "description", "review", "class", "owner")},
        # Present only for artifacts that report progress. The same fields are in
        # `status.md` below, but structured, so an agent reads the stage instead of
        # parsing prose for it. Named `progress` because `status` is taken by
        # found/not_found on this same entry.
        **({"progress": row["progress"]} if "progress" in row else {}),
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

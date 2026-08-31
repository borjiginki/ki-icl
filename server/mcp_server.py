#!/usr/bin/env python3
"""Throwaway demo harness: the three context-layer tools over stdio or HTTP.

This file exists so the read path can be driven end to end from a real MCP client
today. In production these tools live in ki-mcp as `server/tools/artifact_tools.py`,
registered on the shared `mcp` instance beside the skills tools. Only the import of
`mcp` changes; the three function bodies move verbatim.

    CONTEXT_ROOT=dist/staging python3 server/mcp_server.py          # stdio
    CONTEXT_ROOT=dist/staging python3 server/mcp_server.py --http   # http on :8000
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Run directly (`python3 server/mcp_server.py`) and only server/ lands on sys.path,
# so the repo root has to be put there before `server.artifacts` can be imported.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastmcp import FastMCP  # noqa: E402

from server import artifacts  # noqa: E402
from server.usage import USAGE_LOG, ContextUsageMiddleware, catalog_record  # noqa: E402

mcp = FastMCP(
    name="ki-icl",
    instructions=(
        "Company information lives in domains and is fetched by id, never guessed. "
        "Start with `list_domains` (you need no prior knowledge), then "
        "`get_domain_manifest(domain)` to choose, then `get_artifact(domain, ids)`. "
        "Use these when the user asks what KI group does, offers, or requires, as "
        "opposed to how to perform a task. A `not_found` means no artifact has that "
        "id: say so, and never substitute a similar one."
    ),
)
mcp.add_middleware(ContextUsageMiddleware())


@mcp.tool
def list_domains() -> str:
    """List every company-information domain. Start here; you need no prior knowledge."""
    return json.dumps(artifacts.list_domains_payload(), indent=2)


@mcp.tool
def get_domain_manifest(domain: str) -> str:
    """List one domain's artifacts with descriptions, so you can choose what to fetch.

    Returns no file bodies. Read each `description` as a "when to use" signal, then
    fetch the ones you need with `get_artifact`. An unknown domain returns
    `status: not_found` along with the domains that do exist.

    Args:
        domain: Domain id, exactly as `list_domains` reported it.
    """
    return json.dumps(artifacts.domain_manifest_payload(domain), indent=2)


@mcp.tool
def get_artifact(domain: str, ids: str | list[str], max_file_bytes: int = 1_048_576) -> str:
    """Return the full text of one or more artifacts, by exact id.

    Pass several ids to fetch them in one call. Each is answered independently: an id
    that does not exist comes back as `{"status": "not_found", "id": ...}` while the
    rest return normally.

    A `not_found` means no artifact has that id. Say so. Never substitute a similar
    one, and never answer from memory in its place. Every found artifact carries an
    opaque `version_id`; compare it for equality to detect staleness, never parse it.

    Args:
        domain: Domain id, from `list_domains`.
        ids: One artifact id, or a list of them, exactly as `get_domain_manifest` reported them.
        max_file_bytes: Per-file size cap (default 1 MiB). Larger files are listed in `skipped_files`.
    """
    payload = artifacts.get_artifact_payload(domain, ids, max_file_bytes=max_file_bytes)
    return json.dumps(payload, indent=2)


if __name__ == "__main__":
    print(f"serving {artifacts.ARTIFACTS_ROOT}", file=sys.stderr)
    # One snapshot of what exists, so the dashboard can tell a miss for something that
    # never existed from a miss for something that was deleted.
    USAGE_LOG.write(catalog_record(artifacts.ARTIFACTS_ROOT))
    if "--http" in sys.argv:
        mcp.run(transport="http", host="127.0.0.1", port=8000)
    else:
        mcp.run()

"""One log record per artifact actually looked up, hit or miss.

The miss records are the reason this exists. `list_domains` and `get_domain_manifest`
tell you the corpus was browsed; a run of `not_found` on the same id tells you what
is missing from it, and nothing else in the system carries that signal.

**No tool is aware of this.** It is FastMCP middleware, so adding a tool needs no
logging code and no allowlist entry, and logging cannot fall out of step with the
tool list. Nothing here may raise into a call: a logging bug stays an annoyance.

**No caller identity is recorded.** The POC logs what was looked up, never who
looked it up, so no personal data is processed. If that ever changes, port
ki-mcp's `utils/observability._caller()` rather than writing a second one: it emits
a keyed digest gated on a configured salt, and never an email or a raw object id.

One deliberate departure from ki-mcp's middleware, which never parses a tool's
return value: `records_from_call` does. Hit versus miss is the whole signal and it
exists only in the payload. The coupling is confined to that one function, and it
lives here beside the tools rather than in a general-purpose logging module.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, TextIO

from fastmcp.server.middleware import Middleware, MiddlewareContext

CONTEXT_TOOLS = frozenset({"list_domains", "get_domain_manifest", "get_artifact"})

# Empty disables the file sink and leaves stderr as the only one, which is what
# production wants: stdout/stderr is already collected and a file would be a second
# store to own.
USAGE_LOG_PATH = os.environ.get("CONTEXT_USAGE_LOG", "logs/usage.jsonl").strip()


def records_from_call(tool: str, arguments: dict, payload: dict) -> list[dict[str, Any]]:
    """The usage records one tool call produced. Empty for a tool we do not track."""
    if tool not in CONTEXT_TOOLS:
        return []
    base = {"event": "context_use", "tool": tool}

    if tool == "list_domains":
        return [{**base, "outcome": "found", "domain_count": len(payload.get("domains", []))}]

    domain = payload.get("domain") or arguments.get("domain")

    if payload.get("status") == "not_found":  # the domain itself is unknown
        return [{**base, "domain": domain, "outcome": "not_found"}]

    if tool == "get_domain_manifest":
        return [
            {
                **base,
                "domain": domain,
                "outcome": "found",
                "artifact_count": len(payload.get("artifacts", [])),
            }
        ]

    records = []
    for entry in payload.get("artifacts", []):
        record = {**base, "domain": domain, "id": entry.get("id"), "outcome": entry.get("status")}
        if entry.get("status") == "found":
            record["version_id"] = entry.get("version_id")
            record["file_count"] = entry.get("file_count")
        records.append(record)
    return records


class UsageLog:
    """Appends JSON lines to `path` and/or writes them to `stream`. Never raises."""

    def __init__(self, path: Path | None, stream: TextIO | None = sys.stderr) -> None:
        self.path = path
        self.stream = stream

    def write(self, record: dict[str, Any]) -> None:
        try:
            line = json.dumps({"ts": _now(), **record})
        except (TypeError, ValueError):
            return  # an unserialisable record is dropped, not raised
        try:
            if self.path is not None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
            if self.stream is not None:
                print(line, file=self.stream, flush=True)
        except OSError:
            pass


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"


USAGE_LOG = UsageLog(path=Path(USAGE_LOG_PATH) if USAGE_LOG_PATH else None)


class ContextUsageMiddleware(Middleware):
    """Emits one record per artifact looked up, for every context tool call."""

    async def on_call_tool(self, context: MiddlewareContext, call_next) -> Any:
        started = time.perf_counter()
        result = await call_next(context)
        try:
            name = getattr(context.message, "name", "") or ""
            if name in CONTEXT_TOOLS:
                arguments = getattr(context.message, "arguments", None) or {}
                payload = json.loads(result.content[0].text)
                duration_ms = round((time.perf_counter() - started) * 1000, 1)
                for record in records_from_call(name, arguments, payload):
                    USAGE_LOG.write({**record, "duration_ms": duration_ms})
        except Exception:  # noqa: BLE001 — logging must never break a call
            pass
        return result

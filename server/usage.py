"""One log record per artifact actually looked up, hit or miss.

The miss records are the reason this exists. `list_domains` and `get_domain_manifest`
tell you the corpus was browsed; a run of `not_found` on the same id tells you what
is missing from it, and nothing else in the system carries that signal.

**No tool is aware of this.** It is FastMCP middleware, so adding a tool needs no
logging code and no allowlist entry, and logging cannot fall out of step with the
tool list. Logging must never break a call: every emit path swallows its own errors.
The one thing it must not swallow is the *call's* exception, which is re-raised.

**No caller identity is recorded.** The log says what was looked up, never who looked
it up, so no personal data is processed. `session` is a per-connection random UUID
that is not stable across sessions and cannot be linked to a person; it groups one
conversation without identifying anybody. If real identity is ever wanted, port
ki-mcp's `utils/observability._caller()` rather than writing a second one: it emits a
keyed digest gated on a configured salt, and never an email or a raw object id.

**The question text is deliberately not recorded.** It would be the most useful field
here and it is the one to refuse: free text from a colleague can carry material
covered by an NDA or AVV, and personal data with no Art. 6 basis, and once on disk it
is a store somebody has to own, retain and delete. The requested `id` is a usable
proxy for intent without any of that.

One deliberate departure from ki-mcp's middleware, which never parses a tool's return
value: `records_from_call` does. Hit versus miss is the whole signal and it exists
only in the payload. The coupling is confined to that one function.
"""

from __future__ import annotations

import datetime
import json
import os
import re
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
        offered = [a.get("id") for a in payload.get("artifacts", [])]
        # `offered` against what was then fetched is the only feedback loop on
        # description quality, and descriptions are what discovery rests on.
        return [
            {
                **base,
                "domain": domain,
                "outcome": "found",
                "artifact_count": len(offered),
                "offered": offered,
            }
        ]

    records = []
    for entry in payload.get("artifacts", []):
        record = {**base, "domain": domain, "id": entry.get("id"), "outcome": entry.get("status")}
        if entry.get("status") == "found":
            record["version_id"] = entry.get("version_id")
            record["file_count"] = entry.get("file_count")
            # Bytes, not file count: bytes is what fills a context window, and it is
            # the measure that decides whether an artifact is too big.
            record["bytes"] = sum(f.get("size", 0) for f in entry.get("files", []))
            record["skipped"] = len(entry.get("skipped_files", []))
        records.append(record)
    return records


# A topic is a label, not a sentence. The cap is what stops this becoming a store of
# user questions, which would carry NDA-covered material and personal data with no
# Art. 6 basis. Same shape as an artifact id, so a reported gap reads as a proposed
# filename and can be adopted as one.
MAX_TOPIC_LENGTH = 48
MAX_TOPIC_SEGMENTS = 5
_TOPIC_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def normalise_topic(raw: str) -> str | None:
    """A kebab-case label, or None when the input is not one and cannot become one.

    Forgiving about shape (case, spaces, underscores) and strict about content: an
    agent should not have to guess the exact casing to be heard, but it must not be
    able to post a sentence.
    """
    if not isinstance(raw, str):
        return None
    slug = re.sub(r"[\s_]+", "-", raw.strip().lower())
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    if not slug or len(slug) > MAX_TOPIC_LENGTH:
        return None
    if len(slug.split("-")) > MAX_TOPIC_SEGMENTS:
        return None
    return slug if _TOPIC_PATTERN.match(slug) else None


def gap_record(domain: str, topic: str) -> dict[str, Any] | None:
    """One "the manifest had no answer for this" record. None if the topic is unusable.

    Carries the topic and the domain and nothing else. Never the question that
    prompted it.
    """
    slug = normalise_topic(topic)
    if slug is None:
        return None
    return {"event": "context_gap", "domain": str(domain), "topic": slug}


def catalog_record(root: Path) -> dict[str, Any]:
    """One snapshot of what exists, emitted at startup.

    Without it the dashboard cannot tell a miss for something that never existed from
    a miss for something that was deleted, and cannot show the corpus growing.
    """
    domains = root / "domains"
    artifacts: dict[str, str] = {}
    total = 0
    if domains.is_dir():
        for domain_dir in sorted(d for d in domains.iterdir() if d.is_dir()):
            try:
                manifest = json.loads((domain_dir / "_manifest.json").read_text("utf-8"))
            except (OSError, ValueError):
                continue
            for row in manifest.get("artifacts", []):
                artifacts[f"{domain_dir.name}/{row['id']}"] = row.get("version_id")
        total = sum(f.stat().st_size for f in domains.rglob("*") if f.is_file())
    return {
        "event": "catalog",
        "domain_count": len({key.split("/")[0] for key in artifacts}),
        "artifact_count": len(artifacts),
        "bytes": total,
        "artifacts": artifacts,
    }


class UsageLog:
    """Appends JSON lines to `path` and/or writes them to `stream`. Never raises."""

    def __init__(self, path: Path | None, stream: TextIO | None = sys.stderr) -> None:
        self.path = path
        self.stream = stream

    def write(self, record: dict[str, Any]) -> None:
        try:
            line = json.dumps({"ts": record.pop("ts", None) or _now(), **record})
        except (TypeError, ValueError):
            return  # an unserialisable record is dropped, not raised
        try:
            if self.path is not None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                # Opened per record in append mode: several server processes share one
                # log (stdio spawns one per client), and O_APPEND keeps short lines whole.
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
            if self.stream is not None:
                print(line, file=self.stream, flush=True)
        except OSError:
            pass


def _now() -> str:
    """UTC to the millisecond. Second resolution collapsed a whole batch onto one instant."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


USAGE_LOG = UsageLog(path=Path(USAGE_LOG_PATH) if USAGE_LOG_PATH else None)


def _attr(obj: Any, name: str) -> Any:
    """Read `obj.name`, or None if it is absent *or raises*.

    `Context.session_id` is a property that raises RuntimeError outside a request
    context, which a plain getattr default does not catch. Without this guard one
    raising property silently kills logging for the whole call.
    """
    try:
        return getattr(obj, name, None)
    except Exception:  # noqa: BLE001
        return None


def _correlation(context: MiddlewareContext) -> dict[str, Any]:
    """`session` groups one client connection, `seq` orders calls within it.

    Both come from FastMCP and neither identifies a person: session_id is a random
    UUID minted per connection and never reused. Both are absent when a tool is
    called outside a request context, and the record is still written without them.
    """
    fields: dict[str, Any] = {}
    ctx = _attr(context, "fastmcp_context")

    session = _attr(ctx, "session_id")
    if isinstance(session, str) and session:
        fields["session"] = session[:8]

    request_id = _attr(ctx, "request_id")
    if request_id is not None:
        try:
            fields["seq"] = int(request_id)
        except (TypeError, ValueError):
            fields["seq"] = str(request_id)

    stamp = _attr(context, "timestamp")
    if isinstance(stamp, datetime.datetime):
        fields["ts"] = stamp.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    return fields


def current_correlation() -> dict[str, Any]:
    """The same session/seq fields, read from inside a tool body rather than middleware.

    A tool has no MiddlewareContext, so it reaches the request context the way FastMCP
    intends. Absent outside a request, and the record is still written without it.
    """
    try:
        from fastmcp.server.dependencies import get_context

        ctx = get_context()
    except Exception:  # noqa: BLE001 — no request context
        return {}
    fields: dict[str, Any] = {}
    session = _attr(ctx, "session_id")
    if isinstance(session, str) and session:
        fields["session"] = session[:8]
    request_id = _attr(ctx, "request_id")
    if request_id is not None:
        try:
            fields["seq"] = int(request_id)
        except (TypeError, ValueError):
            fields["seq"] = str(request_id)
    return fields


class ContextUsageMiddleware(Middleware):
    """Emits one record per artifact looked up, for every context tool call."""

    async def on_call_tool(self, context: MiddlewareContext, call_next) -> Any:
        started = time.perf_counter()
        name = getattr(context.message, "name", "") or ""
        try:
            result = await call_next(context)
        except Exception as exc:
            # The call's exception is re-raised. Only the logging of it is swallowed.
            self._emit_error(context, name, exc, started)
            raise

        try:
            if name in CONTEXT_TOOLS:
                arguments = getattr(context.message, "arguments", None) or {}
                payload = json.loads(result.content[0].text)
                shared = {**_correlation(context), "duration_ms": _elapsed(started)}
                for record in records_from_call(name, arguments, payload):
                    USAGE_LOG.write({**shared, **record})
        except Exception:  # noqa: BLE001 — logging must never break a call
            pass
        return result

    def _emit_error(self, context, name: str, exc: Exception, started: float) -> None:
        try:
            if name not in CONTEXT_TOOLS:
                return
            USAGE_LOG.write(
                {
                    **_correlation(context),
                    "event": "context_use",
                    "tool": name,
                    "outcome": "error",
                    "error": f"{type(exc).__name__}: {exc}"[:300],
                    "duration_ms": _elapsed(started),
                }
            )
        except Exception:  # noqa: BLE001
            pass


def _elapsed(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)

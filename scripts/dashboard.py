#!/usr/bin/env python3
"""Serve the usage dashboard. `make dashboard`.

Reads `logs/usage.jsonl` on every poll, so records appear while you test. Needs no
MCP server running: it reads the file, not the server.

All aggregation is here, as pure functions over a list of records, so it is testable
without a browser. `server/dashboard.html` only renders what this hands it.

POC scaffolding. In production the records go to stdout and Log Analytics does this
job, so this is meant to be deleted rather than ported to ki-mcp.
"""

from __future__ import annotations

import datetime
import json
import math
import os
from collections import Counter, defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
LOG = Path(os.environ.get("CONTEXT_USAGE_LOG", ROOT / "logs" / "usage.jsonl"))
PAGE = ROOT / "server" / "dashboard.html"
PORT = int(os.environ.get("DASHBOARD_PORT", "8010"))


def read_records(path: Path) -> list[dict[str, Any]]:
    """Every parseable line. A corrupt one is skipped, never fatal."""
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def filter_records(
    records: list[dict[str, Any]], *, domain: str = "", hours: float = 0, now: str = ""
) -> list[dict[str, Any]]:
    """Narrow to one domain and/or a recent window.

    The `catalog` record always survives a domain filter: without it every miss
    reads as "never written" and a deletion becomes invisible.
    """
    out = records
    if domain:
        out = [
            r
            for r in out
            if r.get("event") == "catalog" or r.get("domain") in ("", None, domain)
        ]
    if hours:
        cutoff = datetime.datetime.fromisoformat(
            (now or _utcnow()).replace("Z", "+00:00")
        ) - datetime.timedelta(hours=hours)
        stamp = cutoff.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        out = [r for r in out if str(r.get("ts", "")) >= stamp]
    return out


def _utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _key(record: dict) -> str:
    """`domain/artifact-id`, or just the domain when the domain itself was the miss."""
    domain, artifact_id = record.get("domain"), record.get("id")
    return f"{domain}/{artifact_id}" if artifact_id else str(domain)


def _pct(values: list[float], p: float) -> float:
    """Nearest-rank percentile: always an actually-observed value.

    `statistics.quantiles` defaults to the exclusive method, which interpolates and
    extrapolates past the data, so a six-sample p95 came out at 31.6 ms when the
    slowest call measured was 19.4 ms. A p95 above max is not defensible to anyone
    reading the chart.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return round(ordered[min(rank, len(ordered)) - 1], 1)


def aggregate(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Everything the page draws, computed once."""
    calls = [r for r in records if r.get("event") == "context_use"]
    fetches = [r for r in calls if r.get("tool") == "get_artifact" and r.get("id")]
    hits = [r for r in fetches if r.get("outcome") == "found"]
    # A request for a domain that does not exist carries no `id`, so it is not a
    # fetch. It is still somebody asking for something that is not there, which is
    # the same demand signal, and it fell through every panel before this.
    missed = [r for r in fetches if r.get("outcome") == "not_found"] + [
        r for r in calls if not r.get("id") and r.get("outcome") == "not_found" and r.get("domain")
    ]

    # Everything the catalog has ever held, so a miss can be told apart from a deletion.
    ever: set[str] = set()
    known_domains: set[str] = set()
    for record in records:
        if record.get("event") == "catalog":
            ever |= set(record.get("artifacts") or {})
            known_domains |= {key.split("/")[0] for key in record.get("artifacts") or {}}

    return {
        "kpi": _kpi(calls, fetches, hits, records),
        "misses": _misses(missed, ever),
        "served": _served(hits),
        "funnel": _funnel(calls),
        "offered": _offered(calls),
        "activity": _activity(fetches),
        "versions": _versions(hits),
        "latency": _latency(calls),
        "size_vs_time": [
            {"bytes": r["bytes"], "duration_ms": r.get("duration_ms", 0), "key": _key(r)}
            for r in hits
            if r.get("bytes") is not None and r.get("duration_ms") is not None
        ],
        "catalog": next(
            (r for r in reversed(records) if r.get("event") == "catalog"), None
        ),
        # Only domains that exist: a mistyped one is a fact about the past, not a
        # place to filter to. A log written before catalog records existed has none,
        # so fall back to whatever the calls mention.
        "domains": sorted(
            known_domains or {r["domain"] for r in calls if r.get("domain")}
        ),
        "record_count": len(records),
    }


def _kpi(calls, fetches, hits, records) -> dict[str, Any]:
    return {
        "lookups": len(fetches),
        "hits": len(hits),
        "hit_rate": round(100 * len(hits) / len(fetches), 1) if fetches else 0,
        "artifacts": len({_key(r) for r in hits}),
        "sessions": len({r["session"] for r in calls if r.get("session")}),
        "bytes": sum(r.get("bytes", 0) for r in hits),
        "errors": sum(1 for r in calls if r.get("outcome") == "error"),
        "records": len(records),
    }


def _misses(missed: list[dict], ever: set[str]) -> list[dict[str, Any]]:
    """The panel that pays for the logging: what people asked for and did not get."""
    grouped: dict[str, list[dict]] = defaultdict(list)
    for record in missed:
        grouped[_key(record)].append(record)
    rows = [
        {
            "key": key,
            "count": len(group),
            "first": min(r.get("ts", "") for r in group),
            "last": max(r.get("ts", "") for r in group),
            "sessions": len({r["session"] for r in group if r.get("session")}),
            # True means it was in the catalog once, so this is a deletion, not a gap.
            "ever_existed": key in ever,
        }
        for key, group in grouped.items()
    ]
    return sorted(rows, key=lambda r: (-r["count"], r["key"]))


def _served(hits: list[dict]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for record in hits:
        grouped[_key(record)].append(record)
    rows = [
        {
            "key": key,
            "count": len(group),
            "bytes": sum(r.get("bytes", 0) for r in group),
            "last": max(r.get("ts", "") for r in group),
            "version_id": max(group, key=lambda r: r.get("ts", "")).get("version_id"),
        }
        for key, group in grouped.items()
    ]
    return sorted(rows, key=lambda r: (-r["count"], r["key"]))


def _funnel(calls: list[dict]) -> dict[str, int]:
    """Counted by session, and conditional: each step counts only sessions that
    reached the previous one.

    An unconditional count is meaningless here, because a session that already knew
    an id and fetched it directly never discovered anything, and counting it as
    "fetched" would hide the drop-off this panel exists to show. Those sessions are
    reported separately as `direct`.

    A call that errored did not give the agent a list, so it does not advance a step.
    """
    steps: dict[str, set[str]] = {
        "list_domains": set(),
        "get_domain_manifest": set(),
        "get_artifact": set(),
    }
    for record in calls:
        session = record.get("session")
        tool = record.get("tool")
        if session and tool in steps and record.get("outcome") != "error":
            steps[tool].add(session)

    listed = steps["list_domains"]
    manifested = listed & steps["get_domain_manifest"]
    fetched = manifested & steps["get_artifact"]
    return {
        "listed": len(listed),
        "manifested": len(manifested),
        "fetched": len(fetched),
        # Sessions that fetched without discovering: the agent already knew the id.
        "direct": len(steps["get_artifact"] - manifested),
    }


def _offered(calls: list[dict]) -> list[dict[str, Any]]:
    """How often each artifact was shown in a manifest against how often it was then
    fetched. The only feedback loop there is on description quality."""
    offered: Counter[str] = Counter()
    chosen: Counter[str] = Counter()
    seen_manifest: dict[str, set[str]] = defaultdict(set)

    for record in calls:
        session = record.get("session")
        if record.get("tool") == "get_domain_manifest" and record.get("offered"):
            for artifact_id in record["offered"]:
                key = f"{record.get('domain')}/{artifact_id}"
                offered[key] += 1
                if session:
                    seen_manifest[session].add(key)
        elif record.get("tool") == "get_artifact" and record.get("outcome") == "found":
            key = _key(record)
            # Only counts as "chosen" if this session actually read the manifest first.
            if session and key in seen_manifest.get(session, set()):
                chosen[key] += 1

    return sorted(
        (
            {"key": key, "offered": n, "chosen": chosen[key]}
            for key, n in offered.items()
        ),
        key=lambda r: (-r["offered"], r["key"]),
    )


def _activity(fetches: list[dict]) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, int]] = defaultdict(lambda: {"found": 0, "missed": 0})
    for record in fetches:
        hour = str(record.get("ts", ""))[:13]
        if not hour:
            continue
        key = "found" if record.get("outcome") == "found" else "missed"
        buckets[hour][key] += 1
    return [{"hour": h, **counts} for h, counts in sorted(buckets.items())]


def _versions(hits: list[dict]) -> list[dict[str, Any]]:
    """What the server SENT, never what a client still holds cached. The log cannot
    see the second thing and the panel must not imply that it can."""
    grouped: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for record in hits:
        if record.get("version_id"):
            grouped[_key(record)][record["version_id"]].append(record)

    rows = []
    for key, versions in grouped.items():
        served = sorted(
            (
                {
                    "version_id": version,
                    "count": len(group),
                    "first": min(r.get("ts", "") for r in group),
                    "last": max(r.get("ts", "") for r in group),
                }
                for version, group in versions.items()
            ),
            key=lambda v: v["first"],
        )
        rows.append({"key": key, "served": served, "changed": len(served) > 1})
    return sorted(rows, key=lambda r: r["key"])


def _latency(calls: list[dict]) -> list[dict[str, Any]]:
    """Successful calls only.

    A call that raised is not a measurement of service time, and one slow failure
    drags p95 far enough to flatten every other bar on a shared scale. The error
    count is reported on its own tile instead.
    """
    grouped: dict[str, list[float]] = defaultdict(list)
    for record in calls:
        if record.get("outcome") == "error":
            continue
        if record.get("tool") and record.get("duration_ms") is not None:
            grouped[record["tool"]].append(float(record["duration_ms"]))
    return sorted(
        (
            {
                "tool": tool,
                "n": len(values),
                "p50": _pct(values, 50),
                "p95": _pct(values, 95),
                "max": round(max(values), 1),
            }
            for tool, values in grouped.items()
        ),
        key=lambda r: -r["n"],
    )


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's name
        if self.path.startswith("/data"):
            query = parse_qs(urlparse(self.path).query)
            records = filter_records(
                read_records(LOG),
                domain=query.get("domain", [""])[0],
                hours=float(query.get("hours", ["0"])[0] or 0),
            )
            self._send(json.dumps(aggregate(records)).encode(), "application/json")
        else:
            self._send(PAGE.read_bytes(), "text/html; charset=utf-8")

    def _send(self, body: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:
        pass  # one line per poll would drown the terminal


def main() -> int:
    print(f"Dashboard on http://127.0.0.1:{PORT}  reading {LOG}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

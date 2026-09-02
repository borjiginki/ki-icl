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
import sys
from collections import Counter, defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
# Run directly (`python3 scripts/dashboard.py`) and only scripts/ lands on sys.path,
# so `server.*` is unimportable and `live_catalog` would fail into its own fallback.
sys.path.insert(0, str(ROOT))
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


CURATION = Path(os.environ.get("CONTEXT_CURATION", ROOT / "logs" / "curation.json"))


# What a suggestion can be marked as. They differ only in what happens when more
# demand arrives afterwards, which is the whole point of having three.
CURATION_STATES = {
    # Not now. Returns if asked for again: a dismissal judges the demand so far, and
    # more demand is new information.
    "dismissed",
    # Written up. Returns, flagged, if it is STILL being missed, because that means
    # the artifact is not reachable and something is broken.
    "resolved",
    # Not a stored state: clears the entry.
    "active",
}
# There is deliberately no `deleted` state. A mark that must hold forever is a
# tombstone: it accumulates, nothing shows it, and it silently swallows the next
# person to ask for the same thing. Deleting for good is `purge`, which goes at the
# records instead, so there is nothing left to remember.
_RETURNS_ON_DEMAND = {"dismissed", "resolved"}


def read_curation(path: Path) -> dict[str, Any]:
    """What has been curated, and at what demand. Missing or corrupt reads as none.

    Each entry is `{key, state, count, at}`, where `count` is the demand at the moment
    the decision was made. That baseline is what lets a decision be revisited when more
    demand arrives, rather than muting a topic forever.

    Kept apart from the usage log on purpose: the log is an append-only record of what
    happened, this is a record of what somebody decided. Mixing them makes both harder
    to reason about, and in production they belong in entirely different places.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"entries": []}

    raw = data.get("entries")
    if raw is None:  # two earlier shapes, both meaning "dismissed"
        raw = [
            {"key": item, "count": 0} if isinstance(item, str) else item
            for item in data.get("dismissed", [])
        ]
    entries = []
    for item in raw or []:
        if not isinstance(item, dict) or not item.get("key"):
            continue
        state = str(item.get("state", "dismissed"))
        entries.append(
            {
                "key": str(item["key"]),
                # An unrecognised state is treated as dismissed rather than dropped:
                # hiding a row for an unknown reason is worse than showing it again.
                "state": state if state in CURATION_STATES - {"active"} else "dismissed",
                "count": int(item.get("count") or 0),
                "at": item.get("at"),
            }
        )
    return {"entries": entries}


def _write_curation(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def curate(path: Path, key: str, state: str, count: int = 0) -> dict[str, Any]:
    """Mark one suggestion. `active` clears the mark and puts it back on the list.

    Re-marking an entry that came back raises the baseline, so "seen it, still not
    writing it" holds until it is asked for again.
    """
    if state not in CURATION_STATES:
        raise ValueError(f"unknown curation state {state!r}")
    current = read_curation(path)
    entries = [e for e in current["entries"] if e["key"] != key]
    if state != "active":
        entries.append(
            {"key": key, "state": state, "count": int(count), "at": _utcnow()}
        )
    current["entries"] = entries
    _write_curation(path, current)
    return current


def _is_demand_for(record: dict[str, Any], key: str) -> bool:
    """True for a record that is why `key` appears as a suggestion.

    The two demand signals, and nothing else: a reported gap, and a lookup that came
    back `not_found`. A successful fetch of the same id is not demand, it is use.
    """
    if record.get("event") == "context_gap" and record.get("topic"):
        return f"{record.get('domain')}/{record.get('topic')}" == key
    if record.get("event") == "context_use" and record.get("outcome") == "not_found":
        return _key(record) == key
    return False


def purge(log_path: Path, curation_path: Path, key: str) -> dict[str, Any]:
    """Delete one suggestion for good, by removing what produced it.

    Dismiss and resolve are filters over the log. Delete cannot be, because a filter
    that must hold forever is invisible accumulating state that swallows whoever asks
    next. So this goes at the source: the demand records for `key` are removed, the
    mark for it is cleared, and one `purge` record is appended saying what went.

    Nothing is left to remember, so nothing can be silently suppressed. If somebody
    asks again afterwards the suggestion returns as a new one, having earned it.

    The audit record is the price of the log no longer being append-only: counts
    change here, and the file has to be able to explain that itself.

    Rewritten through a temporary file and one atomic replace, so a reader never sees
    a half-written log. A record appended by a server process during the rewrite is
    still lost, which is a real race and an accepted one at this scale: purging is a
    deliberate human action and the log is written only on tool calls.
    """
    if not log_path.is_file():
        return {"key": key, "removed": 0}

    kept: list[str] = []
    removed = 0
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            kept.append(line)  # not ours to decide about; it stays
            continue
        if isinstance(record, dict) and _is_demand_for(record, key):
            removed += 1
            continue
        kept.append(line)

    if removed:
        kept.append(json.dumps({"ts": _utcnow(), "event": "purge", "key": key, "removed": removed}))
        temp = log_path.with_suffix(log_path.suffix + ".tmp")
        temp.write_text("\n".join(kept) + "\n", encoding="utf-8")
        temp.replace(log_path)

    curate(curation_path, key, "active")
    return {"key": key, "removed": removed}


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


def aggregate(
    records: list[dict[str, Any]],
    curation: dict[str, Any] | None = None,
    catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Everything the page draws, computed once.

    `catalog` is what the tree holds right now, read from disk. The log carries
    catalog snapshots too, but only one per server start, so clearing the log between
    tests silently killed `now written`, `existed, now gone` and the header count
    until the server next happened to restart. What exists *now* is a question about
    the tree, so it is answered by the tree; the log's snapshots answer only what
    existed *once*, which the tree cannot know.
    """
    marks = {e["key"]: e for e in (curation or {}).get("entries", [])}
    gaps = [r for r in records if r.get("event") == "context_gap" and r.get("topic")]
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
    now: set[str] = set()   # what the catalog holds today, for the resolved claim
    for record in records:
        if record.get("event") == "catalog":
            snapshot = set(record.get("artifacts") or {})
            ever |= snapshot
            known_domains |= {key.split("/")[0] for key in snapshot}
            if catalog is None:  # no live read available: the newest snapshot stands in
                now = snapshot
    if catalog is not None:
        now = set(catalog.get("artifacts") or {})
        ever |= now
        known_domains |= {key.split("/")[0] for key in now}

    visible_misses, curated_misses = _misses(missed, gaps, ever, now, marks)

    return {
        "kpi": _kpi(calls, fetches, hits, records),
        "misses": visible_misses,
        "curated": curated_misses,
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
        "catalog": catalog
        or next((r for r in reversed(records) if r.get("event") == "catalog"), None),
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


def _misses(
    missed: list[dict],
    gaps: list[dict],
    ever: set[str],
    now: set[str],
    marks: dict[str, dict],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """What is wanted and not there, from both signals, minus what you dismissed.

    Two ways a want becomes visible, and they are not equal evidence:

    - `reported`: an agent read the manifest, found no answer, and said so through
      `report_gap`. Deliberate, and the only signal that survives a well-behaved
      agent, which is the common case.
    - `guessed`: an agent asked for an id that does not exist. Incidental, and it also
      catches a stale client asking for something that was deleted.

    Returns (visible, curated) so a mark that keeps being asked for stays reviewable
    rather than vanishing. Nothing is hidden permanently here: a suggestion you never
    want to see again is removed by `purge`, at the records rather than behind a mark.
    """
    grouped: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    for record in missed:
        grouped[_key(record)].append(("guessed", record))
    for record in gaps:
        grouped[f"{record.get('domain')}/{record.get('topic')}"].append(("reported", record))

    rows = []
    for key, entries in grouped.items():
        group = [r for _, r in entries]
        rows.append(
            {
                "key": key,
                "count": len(group),
                "first": min(r.get("ts", "") for r in group),
                "last": max(r.get("ts", "") for r in group),
                "sessions": len({r["session"] for r in group if r.get("session")}),
                "sources": sorted({source for source, _ in entries}),
                # True means it was in the catalog once, so this is a deletion.
                "ever_existed": key in ever,
                # Marking something resolved is a claim; the catalog is the evidence.
                "exists_now": key in now,
            }
        )
    rows.sort(key=lambda r: (-r["count"], r["key"]))

    visible, curated = [], []
    for row in rows:
        entry = marks.get(row["key"])
        if entry is None:
            visible.append(row)
        elif entry["state"] in _RETURNS_ON_DEMAND and row["count"] > entry["count"]:
            # Marked, then asked for again. That is new information, and burying it
            # would make the panel lie by omission — most sharply for `resolved`,
            # where it means the artifact exists but is not reachable.
            visible.append(
                {
                    **row,
                    "returned": True,
                    "was_state": entry["state"],
                    "marked_at_count": entry["count"],
                }
            )
        else:
            curated.append({**row, "state": entry["state"], "marked_at": entry["at"]})
    return visible, curated


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


def live_catalog() -> dict[str, Any] | None:
    """What the served tree holds right now, or None if it cannot be read.

    Reuses the server's own `catalog_record` rather than a second implementation, so
    a live read and a logged snapshot can never disagree about shape. Cheap enough to
    do per poll: it is a stat of a small tree.

    **This bypasses both of the read path's scoping seams, deliberately.** It lists
    every artifact id in the corpus regardless of who may read it, because an operator
    view is about the corpus rather than about a caller. So this dashboard is not
    access-controlled content: it must stay on loopback, or whatever replaces it in
    production has to be behind the same authentication as the server itself. It is POC
    scaffolding to delete rather than port, and this is one more reason why.
    """
    try:
        from server.artifacts import ARTIFACTS_ROOT
        from server.usage import catalog_record

        record = catalog_record(ARTIFACTS_ROOT)
    except Exception:  # noqa: BLE001 — a dashboard must render without the tree
        return None
    # An empty read means CONTEXT_ROOT points somewhere without a tree, not that the
    # corpus is empty. Overriding a real logged snapshot with that would turn a
    # misconfiguration into "nothing exists", which is worse than knowing nothing.
    return record if record["artifact_count"] else None


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's name
        if self.path.startswith("/data"):
            query = parse_qs(urlparse(self.path).query)
            records = filter_records(
                read_records(LOG),
                domain=query.get("domain", [""])[0],
                hours=float(query.get("hours", ["0"])[0] or 0),
            )
            payload = aggregate(
                records,
                curation=read_curation(CURATION),
                catalog=live_catalog(),
            )
            self._send(json.dumps(payload).encode(), "application/json")
        else:
            self._send(PAGE.read_bytes(), "text/html; charset=utf-8")

    def do_POST(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's name
        """Mark one suggestion (`/curate`), or delete it for good (`/purge`).

        `/curate` writes only the curation file. `/purge` is the one thing in the
        system that edits the usage log, which is why it is a separate route and not
        another state: the two have entirely different consequences.
        """
        route = urlparse(self.path).path
        if route not in ("/curate", "/purge"):
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
            key = str(body.get("key", ""))
            state = str(body.get("state", ""))
        except (ValueError, OSError):
            self.send_error(400)
            return
        if not key:
            self.send_error(400)
            return

        if route == "/purge":
            self._send(json.dumps(purge(LOG, CURATION, key)).encode(), "application/json")
            return

        if state not in CURATION_STATES:
            self.send_error(400)
            return
        # Baseline computed here, not taken from the client: it is the demand the
        # person was actually looking at when they made the decision.
        current = aggregate(read_records(LOG), curation=read_curation(CURATION))
        counts = {r["key"]: r["count"] for r in current["misses"] + current["curated"]}
        self._send(
            json.dumps(curate(CURATION, key, state, counts.get(key, 0))).encode(),
            "application/json",
        )

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

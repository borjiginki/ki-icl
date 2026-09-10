#!/usr/bin/env python3
"""Serve the usage dashboard on loopback. `make dashboard`.

Reads `logs/usage.jsonl` on every poll, so records appear while you test. Needs no
MCP server running: it reads the file, not the server.

This file is the loopback host and nothing else. Every function it calls lives in
`server/dashboard.py`, shared with the host the MCP server mounts at /dashboard, so the
two cannot drift into disagreeing about what a number means.
"""

from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# Run directly (`python3 scripts/dashboard.py`) and only scripts/ lands on sys.path, so
# `server.*` is unimportable without this. Proven by
# test_the_dashboard_serves_a_live_catalog_when_run_as_a_script, which launches this the
# way the Makefile does; nothing in-process catches it, because pytest puts the
# repository root on the path itself.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.dashboard import (  # noqa: E402
    CURATION,
    CURATION_STATES,
    LOG,
    PAGE,
    aggregate,
    curate,
    demand_baseline,
    filter_records,
    live_catalog,
    purge,
    query_window,
    read_curation,
    read_records,
)

PORT = int(os.environ.get("DASHBOARD_PORT", "8010"))


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's name
        if self.path.startswith("/data"):
            query = parse_qs(urlparse(self.path).query)
            records = filter_records(
                read_records(LOG),
                **query_window(
                    domain=query.get("domain", [""])[0],
                    hours=query.get("hours", [""])[0],
                ),
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
        self._send(
            json.dumps(curate(CURATION, key, state, demand_baseline(key))).encode(),
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

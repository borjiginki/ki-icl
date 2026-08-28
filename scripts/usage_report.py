#!/usr/bin/env python3
"""Summarise logs/usage.jsonl. `make usage`.

The misses table is the one to read. An id that is asked for repeatedly and never
found is a document somebody needs and nobody has written.
"""

from __future__ import annotations

import collections
import json
import os
import sys
from pathlib import Path

LOG = Path(os.environ.get("CONTEXT_USAGE_LOG", "logs/usage.jsonl"))


def main() -> int:
    if not LOG.is_file():
        print(f"No usage log at {LOG}. Call a tool first.", file=sys.stderr)
        return 1

    records = []
    for line in LOG.read_text(encoding="utf-8").splitlines():
        try:
            records.append(json.loads(line))
        except ValueError:
            continue

    calls = collections.Counter(r["tool"] for r in records if "tool" in r)
    hits = collections.Counter(
        (r.get("domain"), r["id"]) for r in records if r.get("outcome") == "found" and "id" in r
    )
    misses = collections.Counter(
        (r.get("domain"), r["id"]) for r in records if r.get("outcome") == "not_found" and "id" in r
    )

    print(f"\n{len(records)} record(s) in {LOG}\n")
    print("Calls by tool")
    for tool, n in calls.most_common():
        print(f"  {n:5}  {tool}")

    print("\nArtifacts served")
    for (domain, artifact_id), n in hits.most_common() or [((None, "(none)"), 0)]:
        print(f"  {n:5}  {domain}/{artifact_id}" if domain else f"  {n:5}  {artifact_id}")

    print("\nAsked for and NOT found  <- what to write next")
    if not misses:
        print("      -  nothing missed")
    for (domain, artifact_id), n in misses.most_common():
        print(f"  {n:5}  {domain}/{artifact_id}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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

    # Filtered on the event, not on the presence of a `tool` key. `access_denied`
    # records carry `tool` too, so counting every record that has one would inflate
    # every figure here the moment enforcement is switched on. Same reason the misses
    # table is derived only from `context_use`: a denial returns the not_found shape to
    # the caller, and the misses table means "documents somebody needs that nobody has
    # written", not "reads that were refused".
    used = [r for r in records if r.get("event") == "context_use"]
    denials = [r for r in records if r.get("event") == "access_denied"]

    calls = collections.Counter(r["tool"] for r in used if "tool" in r)
    hits = collections.Counter(
        (r.get("domain"), r["id"]) for r in used if r.get("outcome") == "found" and "id" in r
    )
    misses = collections.Counter(
        (r.get("domain"), r["id"]) for r in used if r.get("outcome") == "not_found" and "id" in r
    )
    # Aggregated by what was withheld and why, never by who was refused. See
    # tests/test_purpose_limitation.py.
    refused = collections.Counter(
        (r.get("domain"), r.get("reason"), r.get("effect")) for r in denials
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

    # By domain and reason, never by caller. `observed` means the grant would have been
    # refused and was not: that is the dry run, and a run of them is the signal to
    # widen a grant or to relabel content before enforcing.
    print("\nWithheld by access control  (blocked = applied, observed = dry run)")
    if not refused:
        print("      -  nothing withheld")
    for (domain, reason, effect) in sorted(refused, key=lambda k: -refused[k]):
        print(f"  {refused[(domain, reason, effect)]:5}  {domain or '-'}  {reason}  [{effect}]")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

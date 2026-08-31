"""The `status.md` header format: one definition, two consumers.

`validate_context.py` gates it and `package_context.py` lifts it into the manifest,
so the format has to be defined once or the gate and the derivation will drift.

The three fields exist so that a question *across* projects can be answered from
`get_domain_manifest` in one call. Without them the manifest carries no stage and no
date, and "which projects are at risk" means fetching every project in full and
reading prose. That is why the vocabularies are closed: `in progress`, `ongoing` and
`phase 2` are all answers somebody would write for stage, and none of them compares
to anything.

Nothing here runs at request time. It is a build-time concern, exactly like
`version_id`, which is why the server never imports it.
"""

from __future__ import annotations

import re

# A stage is where the work is. It says nothing about how it is going.
STAGES = ("discovery", "mobilising", "delivery", "handover", "closed", "stopped")

# Health is separate from stage, because a project can be in delivery and in trouble
# at the same time. Collapsing the two is how "in progress" ends up meaning nothing.
HEALTHS = ("on track", "at risk", "blocked")

# `**As of YYYY-MM-DD**`. Required in every file that reports a point in time, because
# `version_id` is opaque by design and cannot carry recency.
AS_OF = re.compile(r"\*\*As of (\d{4}-\d{2}-\d{2})", re.IGNORECASE)
STAGE = re.compile(r"^\*\*Stage:\*\*\s*([^.\n]+?)\s*\.?\s*$", re.IGNORECASE | re.MULTILINE)
HEALTH = re.compile(r"^\*\*Health:\*\*\s*([^.\n]+?)\s*\.?\s*$", re.IGNORECASE | re.MULTILINE)


def parse(text: str) -> dict[str, str | None]:
    """The three header fields, each None when absent or unreadable.

    Never raises and never guesses. A None here becomes a validation error at the
    gate, so a malformed header cannot reach the manifest as a plausible-looking value.
    """
    as_of = AS_OF.search(text)
    stage = STAGE.search(text)
    health = HEALTH.search(text)
    return {
        "as_of": as_of.group(1) if as_of else None,
        "stage": _from(stage, STAGES),
        "health": _from(health, HEALTHS),
    }


def _from(match: re.Match | None, allowed: tuple[str, ...]) -> str | None:
    """The matched value, lowercased, or None if it is not in the closed vocabulary."""
    if match is None:
        return None
    value = " ".join(match.group(1).lower().split())
    return value if value in allowed else None

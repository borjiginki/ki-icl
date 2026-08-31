"""The `status.md` header format, parsed once and consumed by the gate and the packager.

These tests pin the one property that matters: an unreadable header yields None rather
than a plausible-looking value, so it becomes a validation error instead of reaching the
manifest as something an agent would compare against.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import status_header  # noqa: E402

WELL_FORMED = """# P: status

**As of 2026-08-28.**
**Stage:** delivery.
**Health:** at risk.

Prose after it.
"""


def test_a_well_formed_header_parses():
    assert status_header.parse(WELL_FORMED) == {
        "as_of": "2026-08-28",
        "stage": "delivery",
        "health": "at risk",
    }


def test_a_trailing_full_stop_is_optional():
    assert status_header.parse("**Stage:** closed\n")["stage"] == "closed"


def test_case_and_extra_spacing_do_not_matter():
    parsed = status_header.parse("**as of 2026-01-02**\n**Stage:**   On   Track\n")

    assert parsed["as_of"] == "2026-01-02"


def test_an_absent_field_is_none_rather_than_a_guess():
    assert status_header.parse("# P\n\nNothing here.\n") == {
        "as_of": None,
        "stage": None,
        "health": None,
    }


def test_a_value_outside_the_vocabulary_is_dropped_not_carried():
    """`in progress` is what somebody would write, and it compares to nothing."""
    parsed = status_header.parse("**Stage:** in progress.\n**Health:** fine.\n")

    assert parsed["stage"] is None
    assert parsed["health"] is None


def test_stage_and_health_vocabularies_do_not_overlap():
    """A word that could be either would make the two fields ambiguous to a reader."""
    assert not set(status_header.STAGES) & set(status_header.HEALTHS)


def test_a_date_without_the_as_of_wrapper_is_not_picked_up():
    """A date in the prose is not an assessment date, and must not be read as one."""
    assert status_header.parse("The extract slipped to 2026-09-05.\n")["as_of"] is None

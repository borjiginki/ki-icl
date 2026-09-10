"""The control that makes the purpose limitation real rather than a promise.

This log exists to answer "did access control hold". It does not exist to answer "how
much did this person read", and the difference is not a matter of intent: a per-person
read log over `hr` and `finance` content is objectively *suitable for* monitoring
employee behaviour, which triggers §87(1) no. 6 BetrVG co-determination whatever anybody
meant by it. The absence of per-person evaluation is also what keeps this outside Annex
III of the EU AI Act.

A promise in a docstring does not survive a dashboard sprint. A failing test does.
`domains/projects/dhl-cbs/team.md` already refused to create such a record as a side
effect of status reporting; this is the same reasoning applied to the one place that
does attach a record to a person.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
# server/dashboard.py is here because the aggregation moved there so the image could
# import it. A file list is a fragile control exactly when code moves, and moving
# aggregation out from under this tuple would retire the control while still showing
# green. See docs/superpowers/specs/2026-09-10-dashboard-on-the-server-ingress-design.md.
AGGREGATORS = ("scripts/dashboard.py", "scripts/usage_report.py", "server/dashboard.py")


@pytest.mark.parametrize("script", AGGREGATORS)
def test_no_aggregation_groups_by_actor(script: str):
    """No tool in this repo may group, rank or count by who the caller was.

    Looks for the actor field appearing as a key anywhere a script builds a grouping:
    a dict subscript, a `.get`, a Counter comprehension. If a legitimate need for the
    field ever arises here, that is a decision requiring the works council, not a
    passing test.
    """
    tree = ast.parse((REPO_ROOT / script).read_text(encoding="utf-8"))

    offenders = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value in ("actor", "actor_key")
    ]

    assert not offenders, (
        f"{script} refers to the actor field. This log answers 'did access control "
        f"hold', never 'how much did this person read'."
    )


@pytest.mark.parametrize("script", AGGREGATORS)
def test_the_aggregators_do_not_import_identity(script: str):
    """Nothing that summarises usage should be able to resolve a person at all."""
    source = (REPO_ROOT / script).read_text(encoding="utf-8")

    assert "from server import identity" not in source
    assert "server.identity" not in source


def test_the_usage_report_counts_tool_calls_and_not_denials():
    """`access_denied` records carry a `tool` field too. Counting them as tool calls
    would inflate every usage figure the moment enforcement is switched on."""
    from scripts import usage_report

    source = inspect.getsource(usage_report)

    assert "context_use" in source, (
        "usage_report must filter on event == 'context_use'; every record with a `tool` "
        "key is no longer a tool call."
    )


def test_a_denied_read_is_not_recorded_as_a_miss():
    """The miss table means "documents somebody needs that nobody has written". A
    denial returns the not_found shape to the caller, so if denials landed in that
    counter the table would quietly stop meaning that, and it is the stated reason the
    log exists at all."""
    from server import access, usage

    policy = access.Policy(
        roles={"ctx.colleague": {"projects": "internal"}}, mode=access.Mode.ENFORCE
    )
    principal = access.Principal(
        authenticated=True,
        roles=frozenset({"ctx.colleague"}),
        actor="p_x",
        actor_key="k_x",
        tenant=None,
        client=None,
        source="demo",
    )
    rows = [{"id": "secret-one", "sensitivity": "restricted"}]

    records = usage.access_denied_records(
        "get_artifact",
        {"domain": "projects", "ids": ["secret-one"]},
        principal,
        policy,
        lambda _domain: rows,
    )

    assert [r["event"] for r in records] == ["access_denied"]
    assert records[0]["reason"] == "clearance_too_low"
    assert records[0]["effect"] == "blocked"
    # And the caller's payload said not_found, so the two records must be
    # distinguishable by event rather than by outcome.
    assert all(r["event"] != "context_use" for r in records)


def test_an_unwritten_document_is_still_recorded_as_a_miss_and_not_as_a_denial():
    """The other direction: a genuine miss must not be reclassified as a denial, or the
    signal disappears the other way."""
    from server import access, usage

    policy = access.Policy(
        roles={"ctx.colleague": {"projects": "internal"}}, mode=access.Mode.ENFORCE
    )
    principal = access.Principal(
        authenticated=True,
        roles=frozenset({"ctx.colleague"}),
        actor="p_x",
        actor_key="k_x",
        tenant=None,
        client=None,
        source="demo",
    )

    records = usage.access_denied_records(
        "get_artifact",
        {"domain": "projects", "ids": ["never-written"]},
        principal,
        policy,
        lambda _domain: [],
    )

    assert records == []

"""What a caller can see, and everything a filtered payload must not disclose.

Most of these are assertions about *absences*, which is why several go through
`json.dumps` over the whole payload rather than checking a field. The leak this design
was most likely to ship lived inside a `fetch_hint` string, invisible to any test that
inspected `payload["artifacts"]`.

The fixture deliberately holds three shapes that have to stay distinguishable in the
code and indistinguishable in the payload: a domain with mixed row levels, a domain that
is genuinely empty, and a domain the caller may not read at all.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from server import access
from tests.conftest import write_artifact

POLICY_BODY = """\
version: 1
roles:
  ctx.colleague:
    description: Everyone.
    grants:
      company: internal
      finance: internal
      hr: internal
      marketing: internal
      projects: internal
      sales: internal
      value-creation: internal
      value-delivery: internal
  ctx.delivery:
    description: Delivery.
    grants:
      projects: restricted
  ctx.people:
    description: HR.
    grants:
      hr: confidential
"""


def _row(artifact_id: str, sensitivity: str) -> dict:
    return {
        "id": artifact_id,
        "title": artifact_id,
        "kind": "project",
        "description": f"About {artifact_id}.",
        "review": "demo",
        "sensitivity": sensitivity,
        "class": None,
        "owner": None,
        "version_id": f"v-{artifact_id}",
    }


@pytest.fixture
def scoped(tmp_path: Path, monkeypatch):
    """A catalog with a mixed domain, an empty domain, and an unreadable one."""
    from server import artifacts as module

    domains = tmp_path / "domains"

    projects = domains / "projects"
    projects.mkdir(parents=True)
    write_artifact(projects, "open-one", README__md="# Open\n")
    write_artifact(projects, "secret-one", README__md="# Secret\n")
    (projects / "_manifest.json").write_text(
        json.dumps(
            {
                "domain": "projects",
                "description": "What we are working on.",
                "owner": None,
                "artifacts": [_row("open-one", "internal"), _row("secret-one", "restricted")],
            }
        ),
        encoding="utf-8",
    )

    empty = domains / "marketing"
    empty.mkdir(parents=True)
    (empty / "_manifest.json").write_text(
        json.dumps({"domain": "marketing", "description": "Reach.", "owner": None, "artifacts": []}),
        encoding="utf-8",
    )

    hr = domains / "hr"
    hr.mkdir(parents=True)
    write_artifact(hr, "handbook", README__md="# Handbook\n")
    (hr / "_manifest.json").write_text(
        json.dumps(
            {
                "domain": "hr",
                "description": "People.",
                "owner": None,
                "artifacts": [_row("handbook", "confidential")],
            }
        ),
        encoding="utf-8",
    )

    (tmp_path / access.POLICY_FILENAME).write_text(POLICY_BODY, encoding="utf-8")
    monkeypatch.setattr(module, "ARTIFACTS_ROOT", tmp_path)
    return module


POLICY = access.Policy(
    roles={
        "ctx.colleague": {
            d: "internal"
            for d in (
                "company", "finance", "hr", "marketing", "projects", "sales",
                "value-creation", "value-delivery",
            )
        },
        "ctx.delivery": {"projects": "restricted"},
        "ctx.people": {"hr": "confidential"},
    },
    mode=access.Mode.ENFORCE,
)
OBSERVING = access.Policy(roles=POLICY.roles, mode=access.Mode.OBSERVE)


def who(*roles: str) -> access.Principal:
    return access.Principal(
        authenticated=True,
        roles=frozenset(roles),
        actor="p_test",
        actor_key="k_test",
        tenant="t",
        client="c",
        source="demo",
    )


BASELINE = who("ctx.colleague")
DELIVERY = who("ctx.colleague", "ctx.delivery")


# --- the flagship -----------------------------------------------------------


@pytest.mark.parametrize("payload_of", ["list", "manifest"])
def test_no_denied_id_appears_anywhere_in_the_serialised_payload(scoped, payload_of):
    """Over the whole serialised payload, not a chosen field.

    This is the test that catches a leak in a string nobody thought to check, which is
    where the worst one in this design would have lived (`fetch_hint`).

    `get_artifact` is excluded on purpose and covered separately below: it *echoes* the
    ids it was asked for, and an echo of the caller's own input discloses nothing.
    """
    payloads = {
        "list": lambda: scoped.list_domains_payload(principal=BASELINE, policy=POLICY),
        "manifest": lambda: scoped.domain_manifest_payload(
            "projects", principal=BASELINE, policy=POLICY
        ),
    }

    text = json.dumps(payloads[payload_of]())

    assert "secret-one" not in text
    assert "handbook" not in text


def test_a_denied_fetch_discloses_nothing_beyond_the_id_the_caller_supplied(scoped):
    """The echoed id is the caller's own input. What must not appear is anything the
    manifest knew about it: its title, its description, its level, its version."""
    payload = scoped.get_artifact_payload(
        "projects", ["open-one", "secret-one"], principal=BASELINE, policy=POLICY
    )
    entry = next(a for a in payload["artifacts"] if a["id"] == "secret-one")

    assert set(entry) == {"status", "id"}
    assert "v-secret-one" not in json.dumps(payload)
    assert "restricted" not in json.dumps(payload)


def test_a_denied_row_is_not_named_in_the_review_caveat(scoped):
    """The caveat lists unapproved ids inside `fetch_hint`. Both rows here are
    `review: demo`, so a leak would be rendered into that string."""
    payload = scoped.domain_manifest_payload("projects", principal=BASELINE, policy=POLICY)

    assert "NOT APPROVED" in payload["fetch_hint"]
    assert "open-one" in payload["fetch_hint"]
    assert "secret-one" not in payload["fetch_hint"]


def test_artifact_count_counts_only_rows_the_caller_can_read(scoped):
    listing = {d["id"]: d for d in scoped.list_domains_payload(principal=BASELINE, policy=POLICY)["domains"]}

    assert listing["projects"]["artifact_count"] == 1
    assert listing["hr"]["artifact_count"] == 0


def test_a_raised_caller_sees_the_rows_a_baseline_caller_does_not(scoped):
    payload = scoped.domain_manifest_payload("projects", principal=DELIVERY, policy=POLICY)

    assert {a["id"] for a in payload["artifacts"]} == {"open-one", "secret-one"}


# --- the oracles ------------------------------------------------------------


def test_a_fully_denied_domain_is_indistinguishable_from_an_empty_one(scoped):
    """Otherwise the difference is an enumeration oracle: walk the domains and learn
    which ones hold something hidden."""
    filtered = scoped.domain_manifest_payload("hr", principal=BASELINE, policy=POLICY)
    genuinely_empty = scoped.domain_manifest_payload("marketing", principal=BASELINE, policy=POLICY)

    assert filtered["artifacts"] == genuinely_empty["artifacts"] == []
    # Same wording, with only the domain id differing. The hint must be true of both,
    # which is why it says "available to you" rather than "published".
    assert filtered["fetch_hint"].replace("hr", "X") == genuinely_empty["fetch_hint"].replace(
        "marketing", "X"
    )


def test_a_denied_artifact_fetch_is_byte_identical_to_a_genuine_miss(scoped):
    denied = scoped.get_artifact_payload(
        "projects", ["secret-one"], principal=BASELINE, policy=POLICY
    )
    invented = scoped.get_artifact_payload(
        "projects", ["never-existed"], principal=BASELINE, policy=POLICY
    )

    assert denied["artifacts"][0] == {"status": "not_found", "id": "secret-one"}
    assert set(denied["artifacts"][0]) == set(invented["artifacts"][0])


def test_a_denied_fetch_reads_no_file_from_disk(scoped, monkeypatch):
    """Not just filtered on the way out: the bytes must never be read at all."""

    def boom(*_args, **_kwargs):
        raise AssertionError("a denied artifact must not be read from disk")

    monkeypatch.setattr(scoped, "_file_payload", boom)

    payload = scoped.get_artifact_payload(
        "projects", ["secret-one"], principal=BASELINE, policy=POLICY
    )

    assert payload["artifacts"][0]["status"] == "not_found"


def test_the_known_list_names_only_domains_the_caller_can_read(scoped):
    """A usability affordance for a mistyped id, not a security boundary: `forbidden`
    already discloses the domain universe deliberately. Filtered anyway, so a typo
    cannot become a cheaper enumeration than the honest answer."""
    payload = scoped.domain_manifest_payload("invented", principal=BASELINE, policy=POLICY)

    assert payload["status"] == "not_found"
    assert "hr" in payload["known"]  # baseline holds hr at `internal`


# --- forbidden vs not_found -------------------------------------------------


def test_a_domain_the_caller_cannot_read_answers_forbidden_not_not_found(scoped):
    """An honest refusal. The eight domain NAMES are business functions disclosed to any
    authenticated colleague by design; their contents are not. `not_found` would make
    the agent tell the user the domain does not exist, and would manufacture a false gap.
    """
    nobody = who()
    payload = scoped.domain_manifest_payload("projects", principal=nobody, policy=POLICY)

    assert payload["status"] == "forbidden"


def test_a_forbidden_answer_tells_the_agent_not_to_report_a_gap(scoped):
    """Without this every denied domain manufactures demand for a document that exists."""
    payload = scoped.domain_manifest_payload("projects", principal=who(), policy=POLICY)

    assert "report_gap" in payload["fetch_hint"]
    assert "not" in payload["fetch_hint"].lower()


def test_a_forbidden_answer_names_nothing_inside_the_domain(scoped):
    text = json.dumps(scoped.domain_manifest_payload("projects", principal=who(), policy=POLICY))

    assert "open-one" not in text
    assert "secret-one" not in text


def test_a_domain_that_does_not_exist_is_still_not_found(scoped):
    """`forbidden` must not become the answer to everything: a granted domain that is
    simply absent is a miss, and the miss table depends on that staying true."""
    payload = scoped.domain_manifest_payload("finance", principal=BASELINE, policy=POLICY)

    assert payload["status"] == "not_found"


# --- fail closed ------------------------------------------------------------


def test_an_anonymous_caller_sees_no_domains_at_all(scoped):
    payload = scoped.list_domains_payload(principal=access.ANONYMOUS, policy=POLICY)

    assert payload["domains"] == []


def test_an_anonymous_caller_can_fetch_nothing(scoped):
    payload = scoped.get_artifact_payload(
        "projects", ["open-one"], principal=access.ANONYMOUS, policy=POLICY
    )

    assert payload["status"] == "forbidden"


def test_a_deny_all_policy_serves_nothing_to_anybody(scoped):
    """What an unreadable policy file degrades to."""
    payload = scoped.list_domains_payload(
        principal=DELIVERY, policy=access.Policy.deny_all(access.Mode.ENFORCE)
    )

    assert payload["domains"] == []


def test_a_narrower_principal_never_sees_more_than_a_broader_one(scoped):
    """Containment, across all three payloads. The property that would break first if
    filtering were added at the payload sites instead of the seam."""
    for domain in ("projects", "hr", "marketing"):
        narrow = scoped.domain_manifest_payload(domain, principal=BASELINE, policy=POLICY)
        broad = scoped.domain_manifest_payload(domain, principal=DELIVERY, policy=POLICY)
        narrow_ids = {a["id"] for a in narrow.get("artifacts", [])}
        broad_ids = {a["id"] for a in broad.get("artifacts", [])}
        assert narrow_ids <= broad_ids, domain


def test_a_row_with_no_sensitivity_label_is_withheld(scoped, tmp_path):
    """The stale-archive case: an older manifest carries no label. Serving it would make
    the label pointless the first time a deployment lagged."""
    manifest = tmp_path / "domains" / "projects" / "_manifest.json"
    data = json.loads(manifest.read_text())
    for row in data["artifacts"]:
        row.pop("sensitivity", None)
    manifest.write_text(json.dumps(data), encoding="utf-8")

    payload = scoped.domain_manifest_payload("projects", principal=DELIVERY, policy=POLICY)

    assert payload["artifacts"] == []


# --- observe mode -----------------------------------------------------------


def test_observe_mode_withholds_nothing(scoped):
    payload = scoped.domain_manifest_payload("projects", principal=BASELINE, policy=OBSERVING)

    assert {a["id"] for a in payload["artifacts"]} == {"open-one", "secret-one"}


def test_observe_mode_returns_rows_in_the_same_order_enforcement_would_have(scoped):
    """If observing changed row order, the dry run would not be a dry run."""
    observed = scoped.domain_manifest_payload("projects", principal=BASELINE, policy=OBSERVING)
    enforced = scoped.domain_manifest_payload("projects", principal=DELIVERY, policy=POLICY)

    assert [a["id"] for a in observed["artifacts"]] == [a["id"] for a in enforced["artifacts"]]


def test_observe_mode_still_answers_a_denied_domain_normally(scoped):
    """The domain axis is observed too, or the dry run would only cover half the model."""
    payload = scoped.domain_manifest_payload("projects", principal=who(), policy=OBSERVING)

    assert payload.get("status") != "forbidden"


# --- the seams, mechanically ------------------------------------------------


def test_every_payload_function_requires_a_principal():
    """Not defaulted, and keyword-only. A default would make "forgot the principal"
    indistinguishable from "passed the right one" at the call site, which is exactly
    what the seam docstring has always promised against and could not enforce."""
    import inspect

    from server import artifacts

    payload_functions = [
        (name, fn)
        for name, fn in vars(artifacts).items()
        if name.endswith("_payload") and not name.startswith("_") and callable(fn)
    ]
    assert payload_functions, "no payload functions found; the check would pass vacuously"

    for name, fn in payload_functions:
        parameter = inspect.signature(fn).parameters.get("principal")
        assert parameter is not None, f"{name} takes no principal"
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY, name
        assert parameter.default is inspect.Parameter.empty, f"{name} defaults its principal"


def test_the_raw_artifact_list_is_read_in_exactly_one_place():
    """The load-bearing property of filtering at the seam: after redaction there is no
    binding of the unfiltered row list anywhere downstream, so a payload builder cannot
    accidentally reach one."""
    import ast
    import inspect

    from server import artifacts

    tree = ast.parse(inspect.getsource(artifacts))
    reads = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "artifacts"
    ]
    assert len(reads) == 1, f'manifest["artifacts"] is read {len(reads)} times; it must be once'


def test_iter_domains_is_reached_only_through_the_visible_domains_seam():
    """Converts a prose invariant into a mechanical one."""
    import ast
    import inspect

    from server import artifacts

    tree = ast.parse(inspect.getsource(artifacts))
    callers = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and any(
            isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Name)
            and inner.func.id == "iter_domains"
            for inner in ast.walk(node)
        )
    }
    assert callers == {"visible_domains"}, callers

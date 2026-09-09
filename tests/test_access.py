"""The authorization model: the sensitivity ladder, grants, and how roles compose.

Everything here is pure. No server, no transport, no environment. `server/access.py`
answers "may they?" and nothing else, which is what makes these tests a table rather
than a scenario.
"""

from __future__ import annotations

import pytest

from server import access


def principal(*roles: str) -> access.Principal:
    """An authenticated principal holding `roles`. Audit fields are not the subject here."""
    return access.Principal(
        authenticated=True,
        roles=frozenset(roles),
        actor=None,
        actor_key=None,
        tenant="tenant-1",
        client="client-1",
        source="demo",
    )


POLICY = access.Policy(
    roles={
        "ctx.colleague": {d: "internal" for d in ("company", "finance", "hr", "projects")},
        "ctx.delivery": {"projects": "restricted"},
        "ctx.finance": {"finance": "restricted"},
        "ctx.people": {"hr": "confidential"},
    },
    mode=access.Mode.ENFORCE,
)


def row(artifact_id: str, sensitivity: str | None) -> dict:
    """A manifest row, trimmed to what the policy layer reads."""
    r = {"id": artifact_id, "title": artifact_id, "review": "approved"}
    if sensitivity is not None:
        r["sensitivity"] = sensitivity
    return r


# --- the ladder -------------------------------------------------------------


def test_the_ladder_order_is_pinned():
    """The dict's ORDER is the ladder, so reordering it changes who can read what.

    Pinned as an exact tuple because a reorder is invisible in review otherwise: the
    keys are unchanged and only the comparison flips.
    """
    assert tuple(access.SENSITIVITY_LEVELS) == ("internal", "restricted", "confidential")


def test_every_level_carries_a_rationale_so_the_gate_can_explain_itself():
    """Same shape as REVIEW_STATES: the error message is built from these strings."""
    assert all(v and isinstance(v, str) for v in access.SENSITIVITY_LEVELS.values())


# --- grant composition ------------------------------------------------------


def test_a_role_grants_the_level_it_names():
    assert access.granted_level(POLICY, principal("ctx.delivery"), "projects") == "restricted"


def test_a_domain_no_role_names_is_not_granted_at_all():
    """None, not the lowest level. Absence of a grant is a deny, not a default."""
    assert access.granted_level(POLICY, principal("ctx.delivery"), "hr") is None


def test_grants_compose_as_the_maximum_across_roles():
    """Per-domain max. `ctx.colleague` alone gives internal; delivery raises projects."""
    p = principal("ctx.colleague", "ctx.delivery")
    assert access.granted_level(POLICY, p, "projects") == "restricted"
    assert access.granted_level(POLICY, p, "finance") == "internal"


def test_a_second_role_never_removes_access():
    """Monotonicity. Min or intersection would mean joining a project loses you access,
    which is unmanageable and is how authorization gets switched off."""
    solo = principal("ctx.people")
    joined = principal("ctx.people", "ctx.delivery")
    for domain in ("company", "finance", "hr", "projects"):
        before = access.granted_level(POLICY, solo, domain)
        after = access.granted_level(POLICY, joined, domain)
        assert access.rank(after) >= access.rank(before), domain


def test_an_unknown_role_grants_nothing():
    assert access.granted_level(POLICY, principal("ctx.invented"), "projects") is None


def test_an_anonymous_principal_is_granted_nothing_anywhere():
    for domain in ("company", "finance", "hr", "projects"):
        assert access.granted_level(POLICY, access.ANONYMOUS, domain) is None


def test_an_authenticated_principal_with_no_roles_is_granted_nothing():
    """Distinct from anonymous, and it is the fail-closed proof: a valid token is not
    an authorization."""
    assert access.granted_level(POLICY, principal(), "company") is None


# --- domains ----------------------------------------------------------------


def test_a_granted_domain_is_readable():
    assert access.may_read_domain(POLICY, principal("ctx.colleague"), "company")


def test_an_ungranted_domain_is_not_readable():
    assert not access.may_read_domain(POLICY, principal("ctx.delivery"), "hr")


def test_a_domain_absent_from_the_policy_is_not_readable_by_anyone():
    """Runtime fail-closed. The gate also catches this, but the archive and the policy
    deploy separately and can skew."""
    p = principal("ctx.colleague", "ctx.delivery", "ctx.finance", "ctx.people")
    assert not access.may_read_domain(POLICY, p, "marketing")


# --- rows -------------------------------------------------------------------


def test_a_row_at_or_below_the_granted_level_is_readable():
    p = principal("ctx.colleague", "ctx.delivery")
    assert access.may_read_row(POLICY, p, "projects", row("dhl", "internal"))
    assert access.may_read_row(POLICY, p, "projects", row("dhl", "restricted"))


def test_a_row_above_the_granted_level_is_denied():
    p = principal("ctx.colleague")
    assert not access.may_read_row(POLICY, p, "projects", row("dhl", "restricted"))


def test_a_row_with_no_sensitivity_field_is_denied():
    """The archive-versus-server skew case: an older archive carries no label. Denying
    it is why observe mode exists."""
    p = principal("ctx.people")
    assert not access.may_read_row(POLICY, p, "hr", row("handbook", None))


def test_a_row_with_an_unknown_sensitivity_is_denied():
    p = principal("ctx.people")
    assert not access.may_read_row(POLICY, p, "hr", row("handbook", "top-secret"))


def test_a_row_in_an_ungranted_domain_is_denied_whatever_its_level():
    p = principal("ctx.delivery")
    assert not access.may_read_row(POLICY, p, "hr", row("handbook", "internal"))


def test_a_broad_role_does_not_reach_personnel_material():
    """There is deliberately no global top level, so this is expressible at all.
    Fails if anyone adds a wildcard or an everything-role."""
    broad = principal("ctx.colleague", "ctx.delivery", "ctx.finance")
    assert not access.may_read_row(POLICY, broad, "hr", row("handbook", "confidential"))
    assert access.may_read_row(POLICY, principal("ctx.people"), "hr", row("handbook", "confidential"))


# --- partitioning -----------------------------------------------------------


def test_partition_rows_splits_readable_from_denied_and_keeps_order():
    rows = [row("a", "internal"), row("b", "restricted"), row("c", "internal")]
    kept, denied = access.partition_rows(POLICY, principal("ctx.colleague"), "projects", rows)
    assert [r["id"] for r in kept] == ["a", "c"]
    assert [r["id"] for r in denied] == ["b"]


def test_partition_rows_denies_everything_in_an_ungranted_domain():
    rows = [row("a", "internal"), row("b", "internal")]
    kept, denied = access.partition_rows(POLICY, principal("ctx.delivery"), "hr", rows)
    assert kept == []
    assert len(denied) == 2


# --- deny reasons -----------------------------------------------------------


def test_the_reason_for_an_unauthenticated_caller_is_that_there_is_no_token():
    assert access.deny_reason(POLICY, access.ANONYMOUS, "company") == "no_token"


def test_the_reason_for_a_roleless_caller_distinguishes_it_from_no_token():
    """Different remedies: sign in, versus ask for a role assignment."""
    assert access.deny_reason(POLICY, principal(), "company") == "no_grants"


def test_the_reason_for_an_ungranted_domain_names_the_domain_not_the_level():
    assert access.deny_reason(POLICY, principal("ctx.delivery"), "hr") == "domain_not_granted"


def test_the_reason_for_a_row_above_clearance_is_the_clearance():
    reason = access.deny_reason(POLICY, principal("ctx.colleague"), "projects", row("dhl", "restricted"))
    assert reason == "clearance_too_low"


def test_every_reason_the_module_can_produce_is_in_the_closed_vocabulary():
    """The closed set is what stops a formatted string carrying a UPN or a question
    onto disk. Same discipline as normalise_topic."""
    cases = [
        (access.ANONYMOUS, "company", None),
        (principal(), "company", None),
        (principal("ctx.delivery"), "hr", None),
        (principal("ctx.colleague"), "projects", row("dhl", "restricted")),
        (principal("ctx.colleague"), "projects", row("dhl", None)),
        (principal("ctx.colleague"), "marketing", None),
    ]
    for p, domain, r in cases:
        assert access.deny_reason(POLICY, p, domain, r) in access.DENY_REASONS


# --- the Principal itself ---------------------------------------------------


def test_a_principal_repr_does_not_print_identifying_fields():
    """A frozen dataclass prints every field, and a TypeError deep in a payload
    function puts the repr into an error string and into the log."""
    p = access.Principal(
        authenticated=True,
        roles=frozenset({"ctx.people"}),
        actor="p_deadbeefcafe",
        actor_key="k_123456",
        tenant="tenant-guid-here",
        client="client-guid-here",
        source="entra",
    )
    text = repr(p)
    assert "p_deadbeefcafe" not in text
    assert "tenant-guid-here" not in text
    assert "client-guid-here" not in text


def test_a_principal_is_hashable_so_it_can_be_a_dict_key_or_memoised():
    assert principal("ctx.people") in {principal("ctx.people")}


def test_a_principal_carries_no_field_for_a_raw_identifier_or_a_token():
    """Structural, not procedural: the class physically cannot hold an oid, a UPN or a
    JWT, so no code path can leak one into the policy layer or the log."""
    fields = set(access.Principal.__dataclass_fields__)
    assert not fields & {"oid", "sub", "upn", "email", "preferred_username", "token", "subject"}


def test_anonymous_is_a_constant_rather_than_none_so_no_read_path_needs_a_null_branch():
    assert isinstance(access.ANONYMOUS, access.Principal)
    assert access.ANONYMOUS.authenticated is False
    assert access.ANONYMOUS.roles == frozenset()


# --- enforce ----------------------------------------------------------------


def test_enforce_drops_the_denied_items():
    kept, denied = [row("a", "internal")], [row("b", "restricted")]
    out = access.enforce(POLICY, kept, denied, key=lambda r: r["id"])
    assert [r["id"] for r in out] == ["a"]


def test_observe_mode_returns_everything_in_the_same_order_as_enforce_would_have_kept():
    """If observe changed row order, the dry run would not be a dry run."""
    observe = access.Policy(roles=POLICY.roles, mode=access.Mode.OBSERVE)
    kept, denied = [row("a", "internal"), row("c", "internal")], [row("b", "restricted")]
    out = access.enforce(observe, kept, denied, key=lambda r: r["id"])
    assert [r["id"] for r in out] == ["a", "b", "c"]


def test_the_access_mode_is_consulted_in_exactly_one_place():
    """One branch, so there is no second code path to diverge from the first."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(access))
    reads = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "mode"
    ]
    assert len(reads) == 1, f"policy.mode is read {len(reads)} times; it must be read once"


# --- loading the policy file ------------------------------------------------

GOOD_POLICY = """
version: 1
roles:
  ctx.colleague:
    description: Everyone.
    grants: {company: internal, hr: internal}
  ctx.people:
    description: HR.
    grants: {hr: confidential}
"""


def write_policy(root, body: str):
    (root / access.POLICY_FILENAME).write_text(body, encoding="utf-8")
    return root


def test_a_policy_file_loads_its_roles_and_grants(tmp_path):
    policy = access.load_policy(write_policy(tmp_path, GOOD_POLICY), mode=access.Mode.ENFORCE)
    assert policy.roles["ctx.people"] == {"hr": "confidential"}
    assert policy.roles["ctx.colleague"]["company"] == "internal"


def test_the_mode_is_carried_onto_the_loaded_policy(tmp_path):
    policy = access.load_policy(write_policy(tmp_path, GOOD_POLICY), mode=access.Mode.OBSERVE)
    assert policy.mode is access.Mode.OBSERVE


def test_a_missing_policy_file_denies_everything_and_never_raises(tmp_path):
    policy = access.load_policy(tmp_path, mode=access.Mode.ENFORCE)
    assert policy.roles == {}
    assert not access.may_read_domain(policy, principal("ctx.colleague"), "company")


def test_an_unparseable_policy_denies_everything_and_never_raises(tmp_path):
    root = write_policy(tmp_path, "roles: [this is a list, not a mapping\n  ]]]")
    policy = access.load_policy(root, mode=access.Mode.ENFORCE)
    assert policy.roles == {}


def test_a_policy_whose_roles_are_not_a_mapping_denies_everything(tmp_path):
    root = write_policy(tmp_path, "version: 1\nroles:\n  - ctx.colleague\n")
    assert access.load_policy(root, mode=access.Mode.ENFORCE).roles == {}


def test_a_grant_naming_a_level_off_the_ladder_is_dropped_rather_than_trusted(tmp_path):
    """Deny-safe. A typo'd level must not become a grant, and must not take the rest of
    the role down with it: the other grants in the same role still load."""
    root = write_policy(
        tmp_path,
        "version: 1\nroles:\n  ctx.colleague:\n    grants: {company: internal, hr: top-secret}\n",
    )
    policy = access.load_policy(root, mode=access.Mode.ENFORCE)
    assert policy.roles["ctx.colleague"] == {"company": "internal"}


def test_a_role_with_no_grants_mapping_loads_as_granting_nothing(tmp_path):
    root = write_policy(tmp_path, "version: 1\nroles:\n  ctx.empty:\n    description: Nothing.\n")
    policy = access.load_policy(root, mode=access.Mode.ENFORCE)
    assert policy.roles["ctx.empty"] == {}


def test_the_repos_own_policy_loads_and_grants_the_known_domains(tmp_path):
    """The real file, not a fixture. It is what the server will actually read."""
    from tests.conftest import REPO_ROOT

    policy = access.load_policy(REPO_ROOT, mode=access.Mode.ENFORCE)
    assert policy.roles, "access-policy.yaml at the repo root must load"
    granted = {d for grants in policy.roles.values() for d in grants}
    assert "team" in granted and "projects" in granted


def test_the_real_policy_gives_nobody_every_domain_at_the_top_level(tmp_path):
    """No global top level, asserted against the shipped file rather than a fixture.
    Fails the day someone adds an everything-role or a wildcard."""
    from tests.conftest import REPO_ROOT

    policy = access.load_policy(REPO_ROOT, mode=access.Mode.ENFORCE)
    all_domains = {d for grants in policy.roles.values() for d in grants}
    for role, grants in policy.roles.items():
        reaches_everything = set(grants) == all_domains and all(
            level == "confidential" for level in grants.values()
        )
        assert not reaches_everything, f"{role} is a global top level"

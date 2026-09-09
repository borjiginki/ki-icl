"""The local demo identities: their guards, and what each one is there to prove.

The fixture is the real `config/demo_principals.yaml`, not a copy of it. A row that
stops making sense fails this suite rather than rotting quietly, and the structural
test at the bottom is what stops somebody tidying away the row the fail-closed proof
rests on.
"""

from __future__ import annotations

import pytest

from server import access, demo_principals, identity
from tests.conftest import REPO_ROOT

REAL_TABLE = REPO_ROOT / "config" / "demo_principals.yaml"
POLICY = access.load_policy(REPO_ROOT, mode=access.Mode.ENFORCE)


def row(sensitivity: str) -> dict:
    return {"id": "x", "sensitivity": sensitivity}


def principal_named(demo_id: str) -> access.Principal:
    """The Principal a demo token resolves to, through the production claim mapper."""
    table = demo_principals.load(REAL_TABLE)
    claims = next(c for c in table.values() if c["demo_id"] == demo_id)
    return identity.principal_from_claims(claims, source="demo")


# --- the shape the verifier needs -------------------------------------------


def test_the_table_loads_as_a_token_to_claims_mapping():
    """Exactly what StaticTokenVerifier takes: token string -> claims dict."""
    table = demo_principals.load(REAL_TABLE)

    assert table
    assert all(isinstance(token, str) for token in table)
    assert all("client_id" in claims and "scopes" in claims for claims in table.values())


def test_the_claims_pass_through_to_the_production_mapper_unchanged():
    """StaticTokenVerifier hands its whole per-token dict to AccessToken.claims, so the
    demo path runs `principal_from_claims` rather than a mock of it. That is the entire
    reason this table is worth having."""
    p = principal_named("delivery")

    assert p.authenticated
    assert "ctx.delivery" in p.roles


def test_every_demo_token_is_recognisable_as_one_at_a_glance():
    """So a misconfiguration is greppable and no demo token can be mistaken for a real
    one in a log line or a support ticket."""
    assert all(t.startswith("demo-token-") for t in demo_principals.load(REAL_TABLE))


# --- the guards -------------------------------------------------------------


def test_a_table_not_marked_local_is_refused(tmp_path):
    """The kill switch. Everything else about this file is convenience; this is the one
    line that stops it being a production identity source."""
    path = tmp_path / "demo.yaml"
    path.write_text(
        "environment: production\nprincipals:\n"
        "  - {id: a, token: demo-token-a, oid: '1', roles: [], scopes: [context.read]}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="local"):
        demo_principals.load(path)


def test_a_row_whose_token_lacks_the_demo_prefix_is_refused(tmp_path):
    path = tmp_path / "demo.yaml"
    path.write_text(
        "environment: local\nprincipals:\n"
        "  - {id: a, token: prod-token-a, oid: '1', roles: [], scopes: [context.read]}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="demo-token-"):
        demo_principals.load(path)


def test_a_missing_table_is_refused_rather_than_treated_as_empty(tmp_path):
    """An empty token map would let StaticTokenVerifier start and reject everything,
    which looks like a policy problem and is actually a missing file."""
    with pytest.raises(ValueError):
        demo_principals.load(tmp_path / "absent.yaml")


def test_the_demo_table_lives_outside_the_corpus_so_it_can_never_be_packaged():
    """`package_context.py` walks `domains/` and copies the policy by name; it has no
    path by which this file could reach an archive."""
    assert "domains" not in REAL_TABLE.relative_to(REPO_ROOT).parts


def test_the_demo_table_is_absent_from_a_packaged_tree(tmp_path):
    from scripts.package_context import build

    out, stage = tmp_path / "dist", tmp_path / "stage"
    build(REPO_ROOT, out, stage)

    assert not list(stage.rglob("demo_principals.yaml"))
    assert not list(out.rglob("demo_principals.yaml"))


# --- what each row proves ---------------------------------------------------


def test_the_broad_principal_reads_widely_and_still_not_personnel_material():
    """The proof there is no global top level. Fails the day somebody adds a wildcard or
    an everything-role to the policy."""
    broad = principal_named("broad")

    assert access.may_read_row(POLICY, broad, "projects", row("restricted"))
    assert access.may_read_row(POLICY, broad, "case-studies", row("restricted"))
    assert not access.may_read_row(POLICY, broad, "team", row("confidential"))


def test_the_delivery_principal_is_raised_in_its_own_compartments_only():
    delivery = principal_named("delivery")

    assert access.may_read_row(POLICY, delivery, "projects", row("restricted"))
    assert not access.may_read_row(POLICY, delivery, "offerings", row("restricted"))
    assert access.may_read_row(POLICY, delivery, "offerings", row("internal"))


def test_the_people_principal_is_the_only_one_that_reaches_personnel_material():
    people = principal_named("people")

    assert access.may_read_row(POLICY, people, "team", row("confidential"))
    for other in ("broad", "delivery", "baseline"):
        assert not access.may_read_row(POLICY, principal_named(other), "team", row("confidential"))


def test_the_baseline_principal_sees_every_domain_at_internal_and_no_higher():
    baseline = principal_named("baseline")
    granted = {d for grants in POLICY.roles.values() for d in grants}

    for domain in granted:
        assert access.may_read_domain(POLICY, baseline, domain), domain
        assert access.may_read_row(POLICY, baseline, domain, row("internal")), domain
        assert not access.may_read_row(POLICY, baseline, domain, row("restricted")), domain


def test_the_no_grants_principal_is_authenticated_and_reads_nothing():
    """The fail-closed proof, and the reason the row exists. A valid token is not an
    authorization."""
    nobody = principal_named("no-grants")

    assert nobody.authenticated
    assert nobody.roles == frozenset()
    granted = {d for grants in POLICY.roles.values() for d in grants}
    assert not any(access.may_read_domain(POLICY, nobody, d) for d in granted)


def test_the_expired_row_is_expired_so_the_401_path_needs_no_crypto():
    """StaticTokenVerifier checks `expires_at`, so demo mode gives a real expired-token
    HTTP test with no RSA key and no clock manipulation."""
    import time

    table = demo_principals.load(REAL_TABLE)
    expired = [c for c in table.values() if c["demo_id"] == "expired"]

    assert expired, "the demo table must keep a row with a dead token"
    assert expired[0]["expires_at"] < time.time()


def test_every_row_proves_something_distinct():
    """Without this, a tidy-up deletes the fail-closed proof and nothing notices.

    Asserted as four distinguishable grant shapes rather than by naming ids, so
    renaming a row is fine and deleting the *coverage* is not.
    """
    table = demo_principals.load(REAL_TABLE)
    shapes = set()
    granted = {d for grants in POLICY.roles.values() for d in grants}

    for claims in table.values():
        p = identity.principal_from_claims(claims, source="demo")
        readable = {d for d in granted if access.may_read_domain(POLICY, p, d)}
        raised = {d for d in granted if access.may_read_row(POLICY, p, d, row("restricted"))}
        top = {d for d in granted if access.may_read_row(POLICY, p, d, row("confidential"))}
        if not readable:
            shapes.add("nothing")
        elif top:
            shapes.add("reaches-confidential")
        elif raised:
            shapes.add("raised-in-some")
        else:
            shapes.add("internal-only")

    assert shapes == {"nothing", "reaches-confidential", "raised-in-some", "internal-only"}

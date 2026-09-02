"""Who is asking: claims in, Principal out, and what reaches the log.

The claim-mapping half is a pure table, and it is the same function for a real Entra
token and a demo one, because `StaticTokenVerifier` passes its claims through unchanged.
That is the point of testing it here rather than only over HTTP: the demo tokens
exercise production code, not a mock of it.
"""

from __future__ import annotations

import pytest

from server import access, identity

TENANT = "11111111-1111-1111-1111-111111111111"
OID = "22222222-2222-2222-2222-222222222222"
KEY = "0123456789abcdef0123456789abcdef"


def claims(**overrides) -> dict:
    """A well-formed delegated user token's claims. Each test overrides one thing."""
    return {
        "oid": OID,
        "tid": TENANT,
        "roles": ["ctx.delivery"],
        "scp": "context.read",
        "azp": "33333333-3333-3333-3333-333333333333",
        "preferred_username": "someone@kigroup.de",
        **overrides,
    }


@pytest.fixture(autouse=True)
def keyed(monkeypatch):
    """A deterministic audit key, so digests are stable and comparable across tests."""
    monkeypatch.setattr(identity, "_AUDIT_KEY", KEY.encode())
    monkeypatch.setattr(identity, "_EXPECTED_TENANT", TENANT)


# --- the happy path ---------------------------------------------------------


def test_a_well_formed_token_becomes_an_authenticated_principal():
    p = identity.principal_from_claims(claims(), source="entra")

    assert p.authenticated
    assert p.roles == frozenset({"ctx.delivery"})
    assert p.source == "entra"


def test_roles_are_lowercased_so_the_policy_file_can_be_written_in_one_case():
    """Entra returns the app-role value as configured, and a display-cased role would
    silently match nothing."""
    p = identity.principal_from_claims(claims(roles=["Ctx.Delivery"]), source="entra")

    assert p.roles == frozenset({"ctx.delivery"})


def test_a_single_string_role_claim_is_accepted_as_well_as_a_list():
    p = identity.principal_from_claims(claims(roles="ctx.delivery"), source="entra")

    assert p.roles == frozenset({"ctx.delivery"})


def test_the_tenant_and_client_are_carried_but_the_username_is_not():
    """`preferred_username` is the UPN: mutable, non-unique, and plain personal data.
    Microsoft documents it as unsuitable for authorization, and it must never be read."""
    p = identity.principal_from_claims(claims(), source="entra")

    assert p.tenant == TENANT
    assert p.client == "33333333-3333-3333-3333-333333333333"
    assert "someone@kigroup.de" not in repr(p)
    assert "someone@kigroup.de" not in str(identity.audit_fields(p))


# --- rejections -------------------------------------------------------------


def test_a_token_from_another_tenant_is_not_authenticated():
    """JWTVerifier validates `iss` but never `tid`, so this check is not redundant, and
    it survives somebody later pointing the issuer at `organizations`."""
    p = identity.principal_from_claims(claims(tid="99999999-9999-9999-9999-999999999999"),
                                       source="entra")

    assert not p.authenticated
    assert p.roles == frozenset()


def test_a_token_with_no_scp_is_rejected_as_app_only():
    """A delegated user token always carries `scp`; an app-only token carries `roles`
    and no `scp`. No human is accountable for an app-only token, so it must not read
    personnel material."""
    c = claims()
    del c["scp"]

    p = identity.principal_from_claims(c, source="entra")

    assert not p.authenticated


def test_a_token_with_no_oid_is_rejected():
    """Without a subject there is nothing to record, and an unattributable read of HR
    content is exactly what the audit trail exists to prevent."""
    c = claims()
    del c["oid"]

    assert not identity.principal_from_claims(c, source="entra").authenticated


def test_a_valid_token_carrying_no_roles_is_authenticated_but_granted_nothing():
    """The fail-closed proof: authentication is not authorization, and the two states
    need different reasons because they have different remedies."""
    p = identity.principal_from_claims(claims(roles=[]), source="entra")

    assert p.authenticated
    assert p.roles == frozenset()
    assert access.deny_reason(access.Policy.deny_all(access.Mode.ENFORCE), p, "hr") == "no_grants"


def test_the_reason_a_principal_was_refused_is_from_the_closed_vocabulary():
    cases = [claims(tid="other"), {**claims(), "scp": ""}, {k: v for k, v in claims().items() if k != "oid"}]
    for c in cases:
        p = identity.principal_from_claims(c, source="entra")
        assert p.deny_reason in access.DENY_REASONS


# --- the keyed digest -------------------------------------------------------


def test_the_same_subject_always_gets_the_same_pseudonym():
    a = identity.principal_from_claims(claims(), source="entra")
    b = identity.principal_from_claims(claims(), source="entra")

    assert a.actor == b.actor
    assert a.actor.startswith("p_")


def test_different_subjects_get_different_pseudonyms():
    other = identity.principal_from_claims(claims(oid="44444444-4444-4444-4444-444444444444"),
                                           source="entra")

    assert other.actor != identity.principal_from_claims(claims(), source="entra").actor


def test_the_pseudonym_never_contains_the_raw_subject():
    p = identity.principal_from_claims(claims(), source="entra")

    assert OID not in p.actor
    assert OID not in str(identity.audit_fields(p))


def test_rotating_the_key_changes_every_pseudonym_and_the_key_id_says_so(monkeypatch):
    """Without a key id you cannot tell a rotation from a hundred new people arriving
    on Tuesday."""
    before = identity.principal_from_claims(claims(), source="entra")
    monkeypatch.setattr(identity, "_AUDIT_KEY", b"f" * 40)
    after = identity.principal_from_claims(claims(), source="entra")

    assert after.actor != before.actor
    assert after.actor_key != before.actor_key
    assert after.actor_key.startswith("k_")


def test_the_digest_is_long_enough_that_two_colleagues_will_not_collide():
    """48 bits. At 1000 principals a 32-bit digest collides with probability ~1e-4, and
    a collision permanently merges two people's read histories: wrong, and a subject
    rights problem the day somebody asks what was recorded about them."""
    p = identity.principal_from_claims(claims(), source="entra")

    assert len(p.actor) == len("p_") + 12


# --- an unset or weak key ---------------------------------------------------


def test_with_no_key_the_actor_fields_are_absent_rather_than_null(monkeypatch):
    """Absent cannot be aggregated by accident. A null or an "unknown" becomes a bucket
    that looks like one very busy person."""
    monkeypatch.setattr(identity, "_AUDIT_KEY", None)
    p = identity.principal_from_claims(claims(), source="entra")

    assert p.actor is None
    assert "actor" not in identity.audit_fields(p)
    assert "actor_key" not in identity.audit_fields(p)


def test_with_no_key_authorization_still_works(monkeypatch):
    """Neither authentication nor authorization may depend on the audit key. A missing
    secret degrades the log; it must never open or close the door."""
    monkeypatch.setattr(identity, "_AUDIT_KEY", None)
    p = identity.principal_from_claims(claims(), source="entra")

    assert p.authenticated
    assert p.roles == frozenset({"ctx.delivery"})


def test_a_key_too_short_to_resist_a_dictionary_attack_is_refused(monkeypatch):
    """Any tenant member can enumerate every colleague's object id, so the candidate set
    is a few hundred. A config mistake must degrade to no identity recorded, never to a
    guessable pseudonym recorded."""
    monkeypatch.delenv("KI_ICL_AUDIT_KEY", raising=False)
    monkeypatch.setenv("KI_ICL_AUDIT_KEY", "tooshort")

    assert identity.audit_key_from_env() is None


def test_a_key_of_sufficient_length_is_accepted(monkeypatch):
    monkeypatch.setenv("KI_ICL_AUDIT_KEY", KEY)

    assert identity.audit_key_from_env() == KEY.encode()


# --- audit fields -----------------------------------------------------------


def test_audit_fields_carry_the_pseudonym_and_the_mode_but_no_roles():
    """Roles are stored once per connection on `access_session`, not on every line:
    data minimisation, and it is the "this person connected with these rights" record
    an access review actually wants."""
    fields = identity.audit_fields(identity.principal_from_claims(claims(), source="entra"))

    assert set(fields) == {"actor", "actor_key", "mode"}
    assert fields["mode"] == "entra"


def test_audit_fields_of_an_anonymous_principal_name_nobody():
    fields = identity.audit_fields(access.ANONYMOUS)

    assert "actor" not in fields
    assert fields["mode"] == "anonymous"


def test_session_fields_carry_the_roles_and_still_no_identifier():
    fields = identity.session_fields(identity.principal_from_claims(claims(), source="entra"))

    assert fields["roles"] == ["ctx.delivery"]
    assert OID not in str(fields)
    assert "someone@kigroup.de" not in str(fields)


# --- outside a request ------------------------------------------------------


def test_current_principal_is_anonymous_when_there_is_no_request():
    """Called from a test, a stdio session, or anywhere else with no HTTP request. It
    must return the constant rather than raise, or the read path grows a null branch."""
    assert identity.current_principal() is access.ANONYMOUS


def test_the_enforcement_mode_defaults_to_enforce_when_the_environment_says_nothing(
    monkeypatch,
):
    """A forgotten variable must not be an open door."""
    monkeypatch.delenv("KI_ICL_ENFORCE", raising=False)
    monkeypatch.delenv("KI_ICL_OBSERVE_UNTIL", raising=False)

    assert identity.enforcement_mode() is access.Mode.ENFORCE


def test_an_unrecognised_enforcement_setting_is_refused_rather_than_guessed(monkeypatch):
    """A safe default covers the security direction; refusing an unknown value catches
    a typo in either direction."""
    monkeypatch.setenv("KI_ICL_ENFORCE", "yes-please")

    with pytest.raises(ValueError):
        identity.enforcement_mode()


def test_observe_mode_requires_an_expiry_date(monkeypatch):
    """Otherwise "we will turn it on next week" is a silent indefinite hole."""
    monkeypatch.setenv("KI_ICL_ENFORCE", "observe")
    monkeypatch.delenv("KI_ICL_OBSERVE_UNTIL", raising=False)

    with pytest.raises(ValueError):
        identity.enforcement_mode()


def test_observe_mode_is_refused_after_its_expiry(monkeypatch):
    """A scheduled, diagnosable outage beats an authorization system that is quietly
    off in production with nothing saying so."""
    monkeypatch.setenv("KI_ICL_ENFORCE", "observe")
    monkeypatch.setenv("KI_ICL_OBSERVE_UNTIL", "2020-01-01")

    with pytest.raises(ValueError):
        identity.enforcement_mode()


def test_observe_mode_is_allowed_before_its_expiry(monkeypatch):
    monkeypatch.setenv("KI_ICL_ENFORCE", "observe")
    monkeypatch.setenv("KI_ICL_OBSERVE_UNTIL", "2099-01-01")

    assert identity.enforcement_mode() is access.Mode.OBSERVE

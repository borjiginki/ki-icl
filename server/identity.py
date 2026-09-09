"""Who is asking. Claims in, `Principal` out, and the only module that holds a raw id.

This is the adapter between the transport and the authorization model. It is the only
file in the repo where a raw Entra object id is ever a live value, which makes
`grep -rn '"oid"' server/` returning one file an auditable invariant. Everything
downstream receives a `Principal`, which has no field capable of holding one.

The same `principal_from_claims` serves a real Entra token and a demo one, because
`StaticTokenVerifier` passes its per-token dict through as `AccessToken.claims`
unchanged. So the demo tokens exercise production code rather than a mock of it, which
is the whole reason the demo table is worth having.

## Why app roles and not groups

The policy is keyed on the `roles` claim. `groups` was rejected on three counts: it
emits GUIDs for cloud-only groups, so the version-controlled grant table would be a
list of opaque identifiers nobody can review; above 200 groups Entra *replaces* the
claim with `_claim_names`/`_claim_sources` pointing at a Graph endpoint, and resolving
that needs the server to hold its own Graph credential *and* to send the caller's
object id to Graph on the read path, which is both a secret this design otherwise does
not have and a personal-data flow it otherwise does not create. App roles have no
overage, and because they are admin-assigned in Enterprise Applications the assignment
list is itself the ISO 27001 A.5.18 access-rights review artefact.

`oid` is the digest input, not `sub`. `sub` is pairwise per app registration: more
private, but it changes if the registration is ever recreated, silently forking one
person's audit history in two, and it cannot be resolved back through Entra at all.
`oid` is immutable per (user, tenant) and is what Entra's own sign-in logs record, so
an incident can be resolved to a person under a documented four-eyes procedure. The
keyed digest already provides the unlinkability `sub` would have bought.
`preferred_username` is never read: Microsoft documents it as mutable and non-unique
and says not to use it for authorization, and it is a UPN, meaning plain personal data.

## Why the audit value is an HMAC and the variable is not called a salt

A salt defends against precomputed tables and may be public. The threat here is a
dictionary attack over a *known candidate set*: any member of the tenant can enumerate
every colleague's `oid` through Graph, so there are only a few hundred candidates to
hash. Against that only a secret key helps. Hence `KI_ICL_AUDIT_KEY`, and hence a key
below `MIN_KEY_CHARS` being treated as absent rather than used: a config mistake must
degrade to "no identity recorded", never to "a guessable pseudonym recorded", which
would be personal data on disk with none of the protection claimed for it.

The truncation length is a collision parameter and contributes nothing to secrecy. 48
bits, because a collision permanently merges two colleagues' read histories: wrong, and
a subject-rights problem the day somebody asks what was recorded about them. `session`
gets away with 8 characters because it is per-connection and disposable.

**One key per deployment, never shared with ki-mcp or anything else.** The digest is
linkable across systems if and only if they share the key, so cross-server correlation
is a new purpose needing its own balancing test, not a config change.

Neither authentication nor authorization ever depends on the audit key. A missing
secret degrades the log; it must never open or close the door.

## Reconciling with ki-mcp's `_caller()`

`server/usage.py` used to say to port ki-mcp's `utils/observability._caller()` rather
than write a second one. That repo is not reachable from this one, so this *is* the
second one, written to the described contract. Two things must match for the pseudonyms
to be comparable, and they are the diff checklist: **the HMAC input string** (the raw
`oid`, unnormalised, behind the purpose prefix) and **the truncation length**. If they
differ, this file changes rather than ki-mcp's: history cannot be rewritten.

## Purpose limitation

This log answers "did access control hold". It does not answer "how much did this
person read", and no tool in this repo aggregates by actor, enforced by
`test_no_aggregation_groups_by_actor`. That is not decoration: a per-person read log
over `team` content is objectively suitable for monitoring employee behaviour, which
triggers §87(1) no. 6 BetrVG co-determination regardless of intent, and the absence of
per-person evaluation is also what keeps this outside Annex III of the AI Act.
`domains/method/project-status-reporting/README.md` refuses to create such a record as
a side effect of status reporting; the same reasoning applies here, in the one place
that does attach a record to a person.

Under stdio there is no token and no principal, and that is honest rather than a gap: a
subprocess the client spawns, whose stdin it owns, running as the invoking user, is
authenticated by OS process ownership. A bearer token there would prove nothing.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import logging
import os
from collections.abc import Mapping
from typing import Any

from server.access import ANONYMOUS, DENY_REASONS, Mode, Principal

log = logging.getLogger(__name__)

# ~128 bits if hex. Below this the key cannot resist a dictionary attack over the
# tenant's few hundred object ids, so it is treated as absent.
MIN_KEY_CHARS = 32

# Domain separation, so the same key can digest something else later without
# cross-collisions and so a key id can never collide with an actor value.
_PURPOSE_ACTOR = b"ki-icl/actor\x00"
_PURPOSE_KEYID = b"ki-icl/keyid\x00"

_ACTOR_CHARS = 12  # 48 bits. A collision parameter, not a security one.
_KEYID_CHARS = 6


def audit_key_from_env() -> bytes | None:
    """The HMAC key, or None when it is unset or too short to be worth using."""
    raw = os.environ.get("KI_ICL_AUDIT_KEY", "").strip()
    if not raw:
        return None
    if len(raw) < MIN_KEY_CHARS:
        log.warning(
            "identity: KI_ICL_AUDIT_KEY is shorter than %d characters and is being "
            "ignored. No actor will be recorded. A guessable pseudonym would be worse "
            "than none.",
            MIN_KEY_CHARS,
        )
        return None
    return raw.encode()


# Module level, read once, monkeypatchable in tests: the same pattern USAGE_LOG_PATH
# uses. `server/access.py` deliberately reads no environment, which is why this lives
# here and the mode is stamped onto the Policy by the composition root.
_AUDIT_KEY: bytes | None = audit_key_from_env()

# Pinned separately from the issuer because JWTVerifier validates `iss` and never
# `tid`. Cheap, and it survives somebody later setting the tenant to `organizations`.
# Unset means no tenant check, which is what demo and local dev need.
_EXPECTED_TENANT: str | None = os.environ.get("KI_ICL_ENTRA_TENANT_ID", "").strip() or None


def _actor(oid: str, key: bytes) -> str:
    return "p_" + hmac.new(key, _PURPOSE_ACTOR + oid.encode(), hashlib.sha256).hexdigest()[
        :_ACTOR_CHARS
    ]


def _key_id(key: bytes) -> str:
    """A short public label for which key is in force.

    Derived from the key rather than configured, so it changes exactly when the key
    does and nobody has to remember to bump it. Publishing 24 bits of HMAC over a fixed
    string, under a 128-bit-plus key, is not a usable oracle.
    """
    return "k_" + hmac.new(key, _PURPOSE_KEYID, hashlib.sha256).hexdigest()[:_KEYID_CHARS]


def _rejected(reason: str, source: str) -> Principal:
    """An unauthenticated principal carrying why. Never raises, never names a value."""
    return Principal(
        authenticated=False,
        roles=frozenset(),
        actor=None,
        actor_key=None,
        tenant=None,
        client=None,
        source=source,
        deny_reason=reason if reason in DENY_REASONS else "unknown",
    )


def _roles(claim: Any) -> frozenset[str]:
    """The `roles` claim as a set. Accepts a list or a bare string, and lowercases.

    Lowercased because Entra returns the app-role value exactly as configured, and a
    display-cased role would silently match nothing in the policy: a deny that looks
    like a policy decision and is actually a typo.
    """
    if isinstance(claim, str):
        claim = [claim]
    if not isinstance(claim, (list, tuple, set, frozenset)):
        return frozenset()
    return frozenset(str(r).strip().lower() for r in claim if str(r).strip())


def principal_from_claims(claims: Mapping[str, Any], *, source: str) -> Principal:
    """Map validated token claims onto a `Principal`. Pure apart from the module key.

    The signature, expiry, issuer and audience were already checked by the verifier
    before this runs; what is left is what the verifier does not look at. Checks run
    outermost first, so a rejection names the most general thing that was wrong.
    """
    if _EXPECTED_TENANT is not None and str(claims.get("tid", "")) != _EXPECTED_TENANT:
        return _rejected("wrong_tenant", source)

    # A delegated user token always carries `scp`; an app-only token carries `roles` and
    # no `scp`. No human is accountable for an app-only token, so it must not read
    # personnel material. The verifier's required_scopes covers this implicitly; doing
    # it here as well is what makes the deny *reason* legible instead of a bare 401.
    if not str(claims.get("scp", "")).strip():
        return _rejected("app_only_token", source)

    oid = str(claims.get("oid", "")).strip()
    if not oid:
        return _rejected("no_subject", source)

    key = _AUDIT_KEY
    return Principal(
        authenticated=True,
        roles=_roles(claims.get("roles")),
        actor=_actor(oid, key) if key else None,
        actor_key=_key_id(key) if key else None,
        tenant=str(claims.get("tid", "")) or None,
        # `azp`, or `appid` on a v1 token. Never AccessToken.client_id, which falls back
        # to `claims["sub"]` in FastMCP and so can carry the *user's* subject under a
        # field named after the app.
        client=str(claims.get("azp") or claims.get("appid") or "") or None,
        source=source,
    )


def current_principal() -> Principal:
    """The caller of the request in flight, or ANONYMOUS outside one.

    Guarded the way `usage.current_correlation()` is, and for the same reason: this is
    called from sync tool bodies, from middleware, and from tests, and under stdio there
    is no request at all. It returns the constant rather than raising, so no read path
    needs a null branch and a resolution failure cannot become an outage.
    """
    try:
        from fastmcp.server.dependencies import get_access_token

        token = get_access_token()
    except Exception:  # noqa: BLE001 - no request context, or no auth configured
        return ANONYMOUS
    if token is None:
        return ANONYMOUS
    try:
        return principal_from_claims(token.claims or {}, source="entra")
    except Exception:  # noqa: BLE001 - a malformed claim set is a denial, not a crash
        log.warning("identity: could not map a token's claims; treating it as anonymous")
        return ANONYMOUS


def audit_fields(principal: Principal) -> dict[str, Any]:
    """What goes on every record: the pseudonym, which key minted it, and the mode.

    The actor keys are *absent* rather than null when there is no audit key. An absent
    key cannot be aggregated by accident, whereas a null or an "unknown" becomes a
    bucket that reads as one very busy person.
    """
    fields: dict[str, Any] = {"mode": principal.source}
    if principal.actor is not None:
        fields["actor"] = principal.actor
    if principal.actor_key is not None:
        fields["actor_key"] = principal.actor_key
    return fields


def session_fields(principal: Principal) -> dict[str, Any]:
    """What goes once per connection: the rights the caller connected with.

    Roles live here rather than on every line. Data minimisation, and it produces an
    explicit "this person connected holding these roles" record, which is the artefact
    an access review wants and which per-line repetition does not give as cleanly.
    """
    fields = audit_fields(principal)
    fields["roles"] = sorted(principal.roles)
    if principal.client is not None:
        fields["client"] = principal.client
    if principal.tenant is not None:
        fields["tenant"] = principal.tenant
    return fields


def auth_configured() -> bool:
    """Whether any authentication is in force. `off` and unset are both "no"."""
    return os.environ.get("KI_ICL_AUTH", "").strip().lower() in ("entra", "demo")


def effective_mode() -> Mode:
    """The mode the read path actually runs in.

    **Authorization is observed, never applied, when there is no authentication to key
    it on.** With no token there is no principal, so enforcing would withhold everything
    from everybody: it would break stdio and `KI_ICL_AUTH=off` completely while
    providing nothing, because a caller who can reach either of those can read the
    corpus files directly. Observe mode is the honest description of that state, it
    needs no second mechanism, and every would-be denial is still recorded with
    `effect: observed` so the dry run is real.

    So `KI_ICL_ENFORCE=1` with auth off observes anyway, and the startup banner says so.
    Enforcement is a property of an authenticated deployment, not a wish.

    `KI_ICL_DEV_PRINCIPAL` is how a developer tests enforcement locally: it supplies an
    identity, so there is something to enforce against.
    """
    if not auth_configured() and not os.environ.get("KI_ICL_DEV_PRINCIPAL", "").strip():
        return Mode.OBSERVE
    return enforcement_mode()


def effective_principal() -> Principal:
    """Who to serve and to record. Applies the local-development fallbacks.

    A real token ALWAYS wins. That is the guard that matters: `KI_ICL_DEV_PRINCIPAL`
    can never escalate an authenticated session, only stand in where there is none.
    And it is refused outright in entra mode by the startup gate, so it cannot stand in
    on a deployment that has real identities.
    """
    from_token = current_principal()
    if from_token.authenticated:
        return from_token
    if auth_configured():
        # Authentication is in force and this caller has none. Fail closed. The
        # transport has already answered 401; this is the defence behind it.
        return from_token

    dev = os.environ.get("KI_ICL_DEV_PRINCIPAL", "").strip()
    if dev:
        return dev_principal(dev)
    return from_token


def dev_principal(name: str) -> Principal:
    """A demo identity adopted locally, so enforcement can be exercised without Entra.

    Stamped `source="dev"`, so a log full of these is unmistakable and no dashboard can
    mistake them for people. Falls back to ANONYMOUS rather than raising if the table or
    the row is missing: a mistyped name must not be a running server with a surprising
    identity.
    """
    try:
        from server import demo_principals

        for claims in demo_principals.load(demo_principals.DEFAULT_TABLE).values():
            if claims.get("demo_id") == name:
                return principal_from_claims(claims, source="dev")
    except Exception as exc:  # noqa: BLE001
        log.warning("identity: KI_ICL_DEV_PRINCIPAL=%s could not be resolved (%s)", name, exc)
        return ANONYMOUS
    log.warning("identity: KI_ICL_DEV_PRINCIPAL=%s names no row in the demo table", name)
    return ANONYMOUS


def enforcement_mode() -> Mode:
    """Whether grants are applied or merely recorded. Raises on anything unrecognised.

    A safe default covers the security direction; refusing an unrecognised value catches
    a typo in *either* direction, including one that would have silently enforced.

    Observe mode needs an expiry and refuses to run past it. Five lines that turn "we
    will turn it on next week" from a silent indefinite hole into a scheduled,
    diagnosable outage, which matters because the failure mode is the whole
    authorization system being off in production with nothing saying so.
    """
    raw = os.environ.get("KI_ICL_ENFORCE", "").strip().lower()
    if raw in ("", "1", "enforce", "true", "yes"):
        return Mode.ENFORCE
    if raw != "observe":
        raise ValueError(
            f"KI_ICL_ENFORCE is {raw!r}. Use `enforce` (the default) or `observe`. "
            f"An unrecognised value is refused rather than guessed."
        )

    until = os.environ.get("KI_ICL_OBSERVE_UNTIL", "").strip()
    if not until:
        raise ValueError(
            "KI_ICL_ENFORCE=observe needs KI_ICL_OBSERVE_UNTIL=YYYY-MM-DD. Observation "
            "without an end date is an authorization system that is off indefinitely "
            "with nothing to say so."
        )
    try:
        expiry = datetime.date.fromisoformat(until)
    except ValueError as exc:
        raise ValueError(f"KI_ICL_OBSERVE_UNTIL is not a YYYY-MM-DD date: {exc}") from exc
    if expiry < datetime.date.today():
        raise ValueError(
            f"KI_ICL_OBSERVE_UNTIL was {until}, which has passed. Observation has "
            f"expired: enforce, or set a new date deliberately."
        )
    return Mode.OBSERVE

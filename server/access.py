"""Who may read what. The authorization model, and nothing else.

Two axes, and they are not interchangeable. **Domains are compartments**: a role reads
a domain or it does not. **`sensitivity` is a ladder**, compared only *within* a domain.
Both are needed because a domain is not a uniform bucket: `finance` holds the expense
policy every employee reads and will hold margin data almost nobody does.

Composition across a principal's roles is the **per-domain maximum**. It is the only
composition under which gaining a role is monotone; min or intersection would mean
joining a project can lose you access, which is how authorization gets switched off.

There is deliberately no wildcard and no global top level. That is what makes "HR reads
personnel material and leadership does not" expressible at all, and it is what lets the
gate treat a new domain as undeployable until somebody decides who reads it.

Two hard constraints on this module, both load-bearing:

1. **It imports no FastMCP and reads no environment.** That is what preserves
   `server/artifacts.py`'s no-FastMCP invariant while letting it call in here, and what
   makes this a table of pure functions rather than a scenario. Before the corpus
   moved to its own repo (ki-ccl), that purity also let `scripts/validate_context.py`
   import `SENSITIVITY_LEVELS` from this exact definition, so the gate could never
   accept a value the server would not - "`scripts/` importing `server/` is acceptable
   only because this module is pure" was the reasoning. The corpus split broke the
   import; `POLICY_FILENAME` and `SENSITIVITY_LEVELS` below are now a small, explicitly
   commented, hand-kept mirror in ki-ccl's `scripts/validate_context.py` instead of a
   shared definition - see the comment there for what that costs and why it is
   bounded (the runtime loader fails safe on a mismatch, never open).
2. **Nothing here raises, and no message names a domain, an artifact, a role or a
   subject.** FastMCP returns exception text to the client verbatim unless
   `mask_error_details` is set, so an exception carrying an artifact id would be the
   leak this module exists to prevent. Predicates return booleans; a broken policy
   becomes a deny-all policy.

It must never import `server/usage.py`. The reverse is how denials get logged: the
middleware re-asks these predicates, which is safe precisely because they are pure.
"""

from __future__ import annotations

import enum
import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Beside `domains/`, never inside it. Under a domain it would be an artifact-adjacent
# file that the read path could plausibly be made to serve one day; at the root of the
# served tree, no read path can reach it, and it still hot-swaps with the corpus when
# the blob cache lands.
POLICY_FILENAME = "access-policy.yaml"

# Lowest first. The ORDER of this dict is the ladder; reordering it changes who can read
# what, which is why test_the_ladder_order_is_pinned asserts the exact tuple. A fourth
# level is a decision, not a convenience, in the same sense KNOWN_DOMAINS is.
#
# The rationale strings are not decoration: ki-ccl's `scripts/validate_context.py`
# builds its error message out of a mirror of them, the same way it does for
# REVIEW_STATES, so an author who picks the wrong level is told what the levels mean
# rather than just which are legal.
SENSITIVITY_LEVELS: Mapping[str, str] = {
    "internal": "any authenticated KI group colleague may read it",
    "restricted": "only the functions named in the policy: customer names, rates, project health",
    "confidential": "a named group only: personnel, works-council, legal and board material",
}

_LADDER: tuple[str, ...] = tuple(SENSITIVITY_LEVELS)

# The closed vocabulary for why a read was refused. Closed because an f-string here
# would be the one place a colleague's question or username could reach disk, and
# because a free-text reason cannot be aggregated. Same discipline as normalise_topic.
DENY_REASONS = frozenset(
    {
        # transport, decided by RequireAuthMiddleware before any of this runs
        "no_token",
        "invalid_token",
        "insufficient_scope",
        # identity, decided in server/identity.py
        "wrong_tenant",
        "app_only_token",
        "no_subject",
        # authorization, decided here
        "no_grants",
        "domain_not_granted",
        "clearance_too_low",
        "unlabelled_artifact",
    }
)


def reason(raw: str) -> str:
    """`raw` if it is a known reason, else `"unknown"`. Never passes text through."""
    return raw if raw in DENY_REASONS else "unknown"


def rank(level: str | None) -> int:
    """Position on the ladder. -1 for None or anything off it, so it compares as lower.

    Safe for a *granted* level, which the loader has already validated. NOT safe on its
    own for an artifact's own level: an unlabelled row would rank -1 and so read as "at
    or below everything". `may_read_row` checks membership before it compares.
    """
    try:
        return _LADDER.index(level)  # type: ignore[arg-type]
    except ValueError:
        return -1


class Mode(enum.Enum):
    """Whether a denial is applied or merely recorded.

    OBSERVE exists for the archive-versus-server skew window, not for caution: the
    enforcing server and the first `sensitivity`-bearing archive deploy independently,
    and a fail-closed server against an older archive hides everything. That also says
    when this can be deleted, namely once such an archive is live.
    """

    ENFORCE = "enforce"
    OBSERVE = "observe"


@dataclass(frozen=True, slots=True)
class Principal:
    """Who is asking. The only identity type anything below the transport knows.

    There is no field for a raw object id, a UPN, an email or a token, and that absence
    is the design: the class physically cannot carry one, so no amount of careless
    logging downstream can leak it. `server/identity.py` is the only module that ever
    holds the raw `oid`, and it emits `actor` already digested.

    `roles` is a frozenset rather than a list because `frozen=True` with a list field
    yields an unhashable dataclass. The identifying fields are `repr=False` because a
    frozen dataclass prints every field it has, and a TypeError deep inside a payload
    function puts that repr into an error string and into the log.

    `authenticated` is deliberately separate from `roles` being empty. "No token" and
    "valid token, no role assignment" have different reasons and different remedies
    (sign in, versus ask an administrator), and collapsing them loses the distinction
    the fail-closed proof rests on.
    """

    authenticated: bool
    roles: frozenset[str]
    actor: str | None = field(repr=False)
    actor_key: str | None = field(repr=False)
    tenant: str | None = field(repr=False)
    client: str | None = field(repr=False)
    source: str
    # Why identity refused this token, when it did. Set by `server/identity.py` and
    # drawn from DENY_REASONS, so a rejection carries its own explanation to the log
    # without anything having to re-derive it from a token it no longer has.
    deny_reason: str | None = None


# A constant rather than None, so no read path needs an `if principal is None` branch
# and no forgotten branch can mean "everyone".
ANONYMOUS = Principal(
    authenticated=False,
    roles=frozenset(),
    actor=None,
    actor_key=None,
    tenant=None,
    client=None,
    source="anonymous",
    deny_reason="no_token",
)


@dataclass(frozen=True, slots=True)
class Policy:
    """The grant table, plus whether it is being applied.

    The mode rides on the policy rather than being read from the environment here, so
    this module stays environment-free and the read path threads one object instead of
    two. The composition root reads the environment and stamps it on.
    """

    roles: Mapping[str, Mapping[str, str]]
    mode: Mode

    @classmethod
    def deny_all(cls, mode: Mode) -> Policy:
        """What an unreadable or malformed policy file becomes. Never an exception."""
        return cls(roles={}, mode=mode)

    def roles_domains(self) -> set[str]:
        """Every domain any role names. The universe this policy has decided about.

        Not the same as what exists on disk: the gate keeps the two in step, but they
        deploy separately and a domain missing from here is invisible at request time
        whatever the tree says.
        """
        return {domain for grants in self.roles.values() for domain in grants}


def load_policy(root: Path, *, mode: Mode) -> Policy:
    """Parse `<root>/access-policy.yaml`. A deny-all policy when it cannot be read.

    Read per request, exactly like `_manifest.json`, and for the same reason: it is a
    kilobyte, and "everything under the served root is fresh" is one mental model with
    no cache to serve one caller's filtering to another.

    Deny-all on failure rather than an exception, because raising here would put the
    text on the client's screen (see the module docstring) and because a server that
    refuses every read is diagnosable while one that raises mid-payload is not. The
    caller is expected to refuse to *start* on a broken policy; this is what keeps a
    file that breaks later from becoming an open door.

    A grant naming a level off the ladder is dropped, not trusted, and the rest of the
    role still loads. A typo must cost that one grant, not silently widen it and not
    take unrelated grants down with it.
    """
    path = root / POLICY_FILENAME
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("access: no usable %s (%s); denying every read", POLICY_FILENAME, exc)
        return Policy.deny_all(mode)
    except Exception as exc:  # noqa: BLE001 - yaml raises its own error tree
        log.warning("access: %s did not parse (%s); denying every read", POLICY_FILENAME, exc)
        return Policy.deny_all(mode)

    return parse_policy(data, mode=mode)


def parse_policy(data: Any, *, mode: Mode) -> Policy:
    """Build a Policy from already-parsed YAML. Deny-all on anything unusable.

    Split from `load_policy` because the parsing is the part with decisions in it, and a
    pure function over a mapping is testable without a filesystem. `load_policy` is then
    just "read the file, hand it here".
    """
    roles_raw = data.get("roles") if isinstance(data, Mapping) else None
    if not isinstance(roles_raw, Mapping):
        log.warning("access: %s has no `roles` mapping; denying every read", POLICY_FILENAME)
        return Policy.deny_all(mode)

    roles: dict[str, dict[str, str]] = {}
    for role, body in roles_raw.items():
        grants_raw = body.get("grants") if isinstance(body, Mapping) else None
        grants: dict[str, str] = {}
        if isinstance(grants_raw, Mapping):
            for domain, level in grants_raw.items():
                if level in SENSITIVITY_LEVELS:
                    grants[str(domain)] = str(level)
                else:
                    log.warning(
                        "access: role %r grants %r at an unknown level; dropping that grant",
                        role,
                        domain,
                    )
        roles[str(role)] = grants
    return Policy(roles=roles, mode=mode)


# --- predicates -------------------------------------------------------------


def granted_level(policy: Policy, principal: Principal, domain: str) -> str | None:
    """The caller's clearance for `domain`, or None when no role grants it at all.

    None rather than the lowest level: absence of a grant is a denial, not a default.
    """
    if not principal.authenticated:
        return None
    best: str | None = None
    for role in principal.roles:
        level = policy.roles.get(role, {}).get(domain)
        if level is not None and rank(level) > rank(best):
            best = level
    return best


def may_read_domain(policy: Policy, principal: Principal, domain: str) -> bool:
    """Whether the domain is visible at all. A domain absent from the policy is not.

    Checked at request time as well as by the gate, because the archive and the policy
    deploy separately and can skew.
    """
    return granted_level(policy, principal, domain) is not None


def may_read_row(
    policy: Policy, principal: Principal, domain: str, row: Mapping[str, Any]
) -> bool:
    """Whether one manifest row is readable.

    A row whose `sensitivity` is missing or off the ladder is denied whatever the
    caller's clearance. That is the stale-archive case, and treating an unlabelled row
    as readable is the one mistake that would make the label pointless.
    """
    level = row.get("sensitivity")
    if level not in SENSITIVITY_LEVELS:
        return False
    granted = granted_level(policy, principal, domain)
    if granted is None:
        return False
    return rank(level) <= rank(granted)


def partition_rows(
    policy: Policy, principal: Principal, domain: str, rows: Iterable[Mapping[str, Any]]
) -> tuple[list[Any], list[Any]]:
    """`(readable, denied)`, each in the order they arrived.

    Both halves are returned because the denied half is the audit record: it is what
    observe mode reports and what enforcement removes, and deriving one from the other
    later would be a second decision that could disagree with this one.
    """
    kept, denied = [], []
    for row in rows:
        (kept if may_read_row(policy, principal, domain, row) else denied).append(row)
    return kept, denied


def deny_reason(
    policy: Policy,
    principal: Principal,
    domain: str,
    row: Mapping[str, Any] | None = None,
) -> str:
    """Why a read was refused, as one word from the closed vocabulary.

    Ordered most general first, so the reason names the outermost thing that was wrong.
    Telling somebody their clearance is too low for an artifact in a domain they cannot
    see at all would be both wrong and a disclosure.
    """
    if not principal.authenticated:
        return "no_token"
    if not principal.roles:
        return "no_grants"
    if granted_level(policy, principal, domain) is None:
        return "domain_not_granted"
    if row is not None and row.get("sensitivity") not in SENSITIVITY_LEVELS:
        return "unlabelled_artifact"
    return "clearance_too_low"


# --- the one place the mode is consulted ------------------------------------


def enforce(
    policy: Policy,
    kept: Sequence[Any],
    denied: Sequence[Any],
    *,
    key: Callable[[Any], str],
) -> list[Any]:
    """Apply a partition, or observe it. The ONLY place `Policy.mode` is read.

    One branch in one function, so there is no second code path to drift from the
    first, and so what observe mode reports is by construction exactly what enforcement
    would remove. Pinned by test_the_access_mode_is_consulted_in_exactly_one_place.

    The sort in the observe branch matters: if observing changed row order, the dry run
    would not be a dry run, and a golden test taken during the observation window would
    not hold after the flip.
    """
    if policy.mode is Mode.OBSERVE:
        return sorted([*kept, *denied], key=key)
    return list(kept)

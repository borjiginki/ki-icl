#!/usr/bin/env python3
"""Report what an Entra token would tell the access layer, without disclosing it.

    az account get-access-token --resource https://graph.microsoft.com | \
        python3 scripts/inspect_claims.py

Answers one question: for a real signed-in colleague, which claims does this tenant
actually emit, and what would `server/identity.py` make of them? That is the thing you
cannot know from documentation, because it depends on how the app registration and the
tenant are configured.

**This never prints the token, the raw object id, or the UPN.** A token is a credential
and belongs in neither a terminal transcript nor a ticket, and the whole point of
`identity.py` is that a raw object id has exactly one home. Personal claims are reported
as present-or-absent plus the keyed pseudonym they would produce. The tenant id is
printed because a tenant GUID identifies an organisation rather than a person, and you
need it to configure the server.

It does not validate anything: no signature check, no expiry check, no audience check.
It is a lens on claim shape, not an authenticator, and it must never become one.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import identity  # noqa: E402

# Claims that identify a person. Reported as present-or-absent, never by value.
PERSONAL = ("oid", "sub", "upn", "unique_name", "preferred_username", "email", "name", "given_name", "family_name")

# What identity.py actually reads, and what each one decides.
READ_BY_IDENTITY = {
    "oid": "the digest input: becomes `actor` in the audit log",
    "tid": "pinned against KI_ICL_ENTRA_TENANT_ID",
    "roles": "THE AUTHORIZATION KEY: matched against access-policy.yaml",
    "scp": "must be non-empty, or the token is rejected as app-only",
    "azp": "recorded as `client`: which app got the token",
    "appid": "the v1 spelling of azp",
}


def _segment(token: str, index: int) -> dict:
    part = token.split(".")[index]
    padded = part + "=" * (-len(part) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))


def main() -> int:
    raw = sys.stdin.read().strip()
    if not raw:
        print("Nothing on stdin. Pipe `az account get-access-token ...` into this.", file=sys.stderr)
        return 1

    # Accept either the CLI's JSON envelope or a bare token.
    try:
        token = json.loads(raw).get("accessToken", "")
    except ValueError:
        token = raw
    if token.count(".") != 2:
        print("That does not look like a JWT (expected three dot-separated parts).", file=sys.stderr)
        return 1

    try:
        header, claims = _segment(token, 0), _segment(token, 1)
    except Exception as exc:  # noqa: BLE001
        print(f"Could not decode the token: {exc}", file=sys.stderr)
        return 1

    issuer = str(claims.get("iss", ""))
    version = "v2" if issuer.endswith("/v2.0") else "v1" if "sts.windows.net" in issuer else "?"

    print("\nToken shape")
    print(f"  signing alg      {header.get('alg')}")
    print(f"  issuer version   {version}   <- the server is configured for v2 only")
    print(f"  tenant (tid)     {claims.get('tid', '(absent)')}")
    print(f"  audience (aud)   {claims.get('aud')}")

    print("\nWhat server/identity.py reads")
    for claim, purpose in READ_BY_IDENTITY.items():
        value = claims.get(claim)
        if claim in PERSONAL:
            shown = "PRESENT (value withheld)" if value else "ABSENT"
        elif claim == "roles":
            shown = f"{value}" if value else "ABSENT"
        elif claim == "scp":
            shown = f"present, {len(str(value).split())} scope(s)" if value else "ABSENT"
        else:
            shown = f"{value}" if value else "ABSENT"
        print(f"  {claim:8} {shown:28} {purpose}")

    print("\nPersonal claims this token carries (values never printed)")
    for claim in PERSONAL:
        if claims.get(claim):
            print(f"  {claim:20} PRESENT")

    print("\nWhat the audit log would record")
    principal = identity.principal_from_claims(claims, source="entra")
    if identity._AUDIT_KEY is None:
        print("  KI_ICL_AUDIT_KEY is unset, so no actor would be recorded at all.")
        print("  Set it (32+ chars) and re-run to see the pseudonym.")
    print(f"  {identity.audit_fields(principal)}")
    print(f"  authenticated={principal.authenticated}  roles={set(principal.roles) or '{}'}"
          f"  deny_reason={principal.deny_reason}")

    print("\nEvery other claim name in this token (names only)")
    others = sorted(set(claims) - set(READ_BY_IDENTITY) - set(PERSONAL))
    print("  " + ", ".join(others))
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

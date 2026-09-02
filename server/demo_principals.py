"""Fake identities for local development, and the guards that keep them local.

`StaticTokenVerifier` takes a `{token_string: claims_dict}` mapping and hands the whole
per-token dict through as `AccessToken.claims`. That one fact is what makes this worth
having: the demo tokens run `identity.principal_from_claims` unchanged, so the demo
path exercises production code rather than a mock of it, and a bug in claim mapping
shows up here rather than the first time a real Entra token arrives.

Five guards, each sufficient on its own, because the whole purpose of this file is to
let somebody in without a real credential:

1. `environment: local` in the table, or the loader refuses it. The kill switch.
2. Every token must start with `demo-token-`, or that row is refused. So a
   misconfiguration is greppable and no demo token can be mistaken for a real one in a
   log line or a support ticket.
3. Loaded only when `KI_ICL_AUTH=demo`, which appears in the process listing and on
   every `server_start` record.
4. Demo mode over HTTP refuses to bind anywhere but loopback.
5. It lives under `config/`, outside `domains/`, so the packager has no path by which
   it could reach an archive. Tested, not assumed.

The `oid` values are obviously synthetic and the tokens are not secrets. Nothing here
is a credential for anything, which is the reason it can be committed at all.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

TOKEN_PREFIX = "demo-token-"
REQUIRED_ENVIRONMENT = "local"

# The table's home lives beside its loader rather than in `mcp_server`, so that
# `identity` can resolve a dev principal without importing the composition root and
# creating an import cycle through it.
DEFAULT_TABLE = Path(__file__).resolve().parent.parent / "config" / "demo_principals.yaml"


def load(path: Path) -> dict[str, dict[str, Any]]:
    """Parse a demo table into the mapping `StaticTokenVerifier` wants.

    Raises rather than returning an empty mapping on any problem. An empty token map
    would let the server start and reject every caller, which reads as a policy problem
    and is actually a missing file: the failure has to name itself.
    """
    import yaml

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise ValueError(f"demo principals: cannot read {path} ({exc})") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"demo principals: {path} did not parse ({exc})") from exc

    if not isinstance(data, dict):
        raise ValueError(f"demo principals: {path} is not a YAML mapping")

    environment = str(data.get("environment", "")).strip()
    if environment != REQUIRED_ENVIRONMENT:
        raise ValueError(
            f"demo principals: {path} declares environment {environment!r}. Only "
            f"{REQUIRED_ENVIRONMENT!r} is accepted. These are fake identities and they "
            f"must never be an identity source anywhere a real one exists."
        )

    tenant = str(data.get("tenant", "")).strip() or None
    client = str(data.get("client", "")).strip() or None

    rows = data.get("principals")
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"demo principals: {path} has no `principals` list")

    table: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f"demo principals: {path} has a principal that is not a mapping")
        token = str(row.get("token", ""))
        if not token.startswith(TOKEN_PREFIX):
            raise ValueError(
                f"demo principals: token {token!r} must start with {TOKEN_PREFIX!r}, so "
                f"a demo credential is recognisable as one wherever it turns up."
            )
        scopes = row.get("scopes") or []
        claims: dict[str, Any] = {
            # StaticTokenVerifier reads these two and passes everything through.
            "client_id": client or "demo-client",
            "scopes": list(scopes),
            # What identity.principal_from_claims reads, shaped exactly as Entra would.
            "oid": str(row.get("oid", "")),
            "tid": tenant,
            "roles": list(row.get("roles") or []),
            "scp": " ".join(str(s) for s in scopes),
            "azp": client,
            # Not a token claim. Carried so the tests and the startup banner can name a
            # row without reverse-engineering it from the grants.
            "demo_id": str(row.get("id", "")),
        }
        if row.get("expires_at") is not None:
            claims["expires_at"] = row["expires_at"]
        table[token] = claims
    return table

"""Entra config from the environment, and a JWT verifier that pins tenant and scopes.

This module is the resource-server half only. It resolves tenant, client, audience and
scope from `AZURE_*` / `MCP_BASE_URL`, still accepting the `KI_ICL_ENTRA_*` aliases.
`EntraJWTVerifier` is a `JWTVerifier` that also:
- accepts Entra's full-URI `scp` values by adding the short suffix, and
- refuses a token whose `tid` is not the configured tenant.

A rejection logs the generic string `Entra bearer token rejected` and nothing else.
Claims, `oid`, the raw token, and email must never appear in that line: the parent
verifier already has a habit of naming `client_id` on mismatch, and this check exists
so a wrong-tenant token cannot become an identity leak on the way out.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from fastmcp.server.auth.providers.jwt import JWTVerifier
from mcp.server.auth.provider import AccessToken

from server.auth_env import canonical_or_legacy

log = logging.getLogger(__name__)

_TENANT_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_LOGIN = "https://login.microsoftonline.com"


@dataclass(frozen=True)
class EntraConfig:
    tenant_id: str
    client_id: str
    base_url: str
    audiences: list[str]
    scope: str
    required_scope: str

    @property
    def authorization_server(self) -> str:
        return f"{self.base_url.rstrip('/')}/"

    @property
    def issuer(self) -> str:
        return f"{_LOGIN}/{self.tenant_id}/v2.0"

    @property
    def jwks_uri(self) -> str:
        return f"{_LOGIN}/{self.tenant_id}/discovery/v2.0/keys"

    @property
    def authorize_url(self) -> str:
        return f"{_LOGIN}/{self.tenant_id}/oauth2/v2.0/authorize"

    @property
    def token_url(self) -> str:
        return f"{_LOGIN}/{self.tenant_id}/oauth2/v2.0/token"

    @property
    def default_authorize_scope(self) -> str:
        return f"openid offline_access {self.scope}"

    @property
    def scope_resource(self) -> str:
        if "/" not in self.scope:
            return self.scope
        return self.scope.rsplit("/", 1)[0]


def _origin_from_base_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    if (
        not parsed.scheme
        or not parsed.netloc
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        raise SystemExit(
            "MCP_BASE_URL must be an origin (scheme + host, no path, query, or fragment)."
        )
    return f"{parsed.scheme}://{parsed.netloc}"


def entra_config_from_env() -> EntraConfig:
    tenant_id = canonical_or_legacy("AZURE_TENANT_ID", "KI_ICL_ENTRA_TENANT_ID")
    client_id = canonical_or_legacy("AZURE_CLIENT_ID", "KI_ICL_ENTRA_CLIENT_ID")
    base_url = canonical_or_legacy("MCP_BASE_URL", "KI_ICL_ENTRA_BASE_URL")

    if not _TENANT_PATTERN.match(tenant_id):
        raise SystemExit("AZURE_TENANT_ID must be a tenant GUID.")
    if not client_id:
        raise SystemExit("entra mode needs AZURE_CLIENT_ID or KI_ICL_ENTRA_CLIENT_ID.")
    if not base_url:
        raise SystemExit("entra mode needs MCP_BASE_URL or KI_ICL_ENTRA_BASE_URL.")

    azure_audience = os.environ.get("AZURE_AUDIENCE", "").strip()
    identifier_uri = (
        azure_audience
        or os.environ.get("KI_ICL_ENTRA_IDENTIFIER_URI", "").strip()
        or f"api://{client_id}"
    )
    if azure_audience:
        audiences = [azure_audience]
    else:
        audiences = []
        for value in (identifier_uri, client_id):
            if value not in audiences:
                audiences.append(value)

    required_scope = "context.read"
    azure_scope = os.environ.get("AZURE_SCOPE", "").strip()
    scope = azure_scope or f"{identifier_uri}/{required_scope}"
    if azure_scope:
        resource = azure_scope.rsplit("/", 1)[0]
        if resource not in audiences:
            raise SystemExit(
                "AZURE_SCOPE resource must be one of the configured audiences."
            )

    return EntraConfig(
        tenant_id=tenant_id,
        client_id=client_id,
        base_url=_origin_from_base_url(base_url),
        audiences=audiences,
        scope=scope,
        required_scope=required_scope,
    )


class EntraJWTVerifier(JWTVerifier):
    """`JWTVerifier` plus a tenant pin and Entra full-URI scope suffixes."""

    def __init__(
        self,
        *,
        tenant_id: str | None = None,
        scope_resource: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.tenant_id = tenant_id
        self.scope_resource = scope_resource

    def _extract_scopes(self, claims: dict[str, Any]) -> list[str]:
        scopes = list(super()._extract_scopes(claims))
        resource = self.scope_resource
        if not resource:
            return scopes
        prefix = f"{resource}/"
        scopes.extend(
            scope[len(prefix) :]
            for scope in list(scopes)
            if scope.startswith(prefix)
        )
        return scopes

    async def load_access_token(self, token: str) -> AccessToken | None:
        access = await super().load_access_token(token)
        if access is None:
            return None
        tid = str((access.claims or {}).get("tid", ""))
        if self.tenant_id is not None and tid != self.tenant_id:
            log.warning("Entra bearer token rejected")
            return None
        return access

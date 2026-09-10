"""Entra config, JWT verification, and the Claude-facing OAuth facade on this host.

Config and `EntraJWTVerifier` are the resource-server half: tenant, client, audience
and scope from `AZURE_*` / `MCP_BASE_URL`, still accepting the `KI_ICL_ENTRA_*` aliases.
The verifier also:
- accepts Entra's full-URI `scp` values by adding the short suffix, and
- refuses a token whose `tid` is not the configured tenant.

The facade half publishes protected-resource and authorization-server metadata on this
origin, redirects `/authorize` to Entra, and proxies `/token` with an allowlist.
It never reads a client secret from the environment and never forwards `login_hint`.

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
from urllib.parse import urlencode, urlparse

import httpx
from fastmcp import FastMCP
from fastmcp.server.auth import RemoteAuthProvider
from fastmcp.server.auth.providers.jwt import JWTVerifier
from mcp.server.auth.provider import AccessToken
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

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


_ENTRA_AUTHORIZE_PARAMS = frozenset(
    {
        "client_id",
        "response_type",
        "redirect_uri",
        "scope",
        "state",
        "code_challenge",
        "code_challenge_method",
        "prompt",
        "domain_hint",
        "response_mode",
        "nonce",
    }
)

_ENTRA_TOKEN_PARAMS = frozenset(
    {
        "grant_type",
        "code",
        "redirect_uri",
        "client_id",
        "client_secret",
        "code_verifier",
        "refresh_token",
        "scope",
    }
)


def _origin(config: EntraConfig) -> str:
    return config.base_url.rstrip("/")


def protected_resource_metadata(config: EntraConfig) -> dict[str, Any]:
    origin = _origin(config)
    return {
        "resource": f"{origin}/mcp",
        "authorization_servers": [config.authorization_server],
        "scopes_supported": [config.scope],
        "bearer_methods_supported": ["header"],
    }


def oauth_metadata(config: EntraConfig) -> dict[str, Any]:
    origin = _origin(config)
    return {
        "issuer": origin,
        "authorization_endpoint": f"{origin}/authorize",
        "token_endpoint": f"{origin}/token",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": [
            "client_secret_post",
            "client_secret_basic",
        ],
        "scopes_supported": ["openid", "offline_access", config.scope],
    }


class EntraAuthProvider(RemoteAuthProvider):
    """Resource-server verifier plus the this-host authorization server Claude talks to."""

    def __init__(self, config: EntraConfig) -> None:
        self.entra_config = config
        verifier = EntraJWTVerifier(
            jwks_uri=config.jwks_uri,
            issuer=config.issuer,
            audience=list(config.audiences),
            required_scopes=[config.required_scope],
            ssrf_safe=True,
            tenant_id=config.tenant_id,
            scope_resource=config.scope_resource,
        )
        super().__init__(
            token_verifier=verifier,
            authorization_servers=[config.authorization_server],
            base_url=config.base_url,
            scopes_supported=[config.scope],
        )


def register_oauth_routes(mcp: FastMCP, config: EntraConfig) -> None:
    """Claude probes these on this host; authorize and token then go to Entra."""

    @mcp.custom_route("/.well-known/oauth-protected-resource", methods=["GET"])
    async def _protected_resource_root(_: Request) -> JSONResponse:
        return JSONResponse(protected_resource_metadata(config))

    @mcp.custom_route("/.well-known/oauth-authorization-server", methods=["GET"])
    async def _authorization_server_metadata(_: Request) -> JSONResponse:
        return JSONResponse(oauth_metadata(config))

    @mcp.custom_route("/authorize", methods=["GET"])
    async def _authorize_redirect(request: Request) -> RedirectResponse:
        params = {
            key: value
            for key, value in request.query_params.items()
            if key in _ENTRA_AUTHORIZE_PARAMS
        }
        if not params.get("scope"):
            params["scope"] = config.default_authorize_scope
        entra = f"{config.authorize_url}?{urlencode(params)}"
        request.scope["query_string"] = b""
        return RedirectResponse(entra, status_code=302)

    @mcp.custom_route("/token", methods=["POST"])
    async def _token_proxy(request: Request) -> Response:
        form = await request.form()
        raw = {key: str(value) for key, value in form.multi_items()}
        body = {key: value for key, value in raw.items() if key in _ENTRA_TOKEN_PARAMS}
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                upstream = await client.post(config.token_url, data=body)
        except httpx.HTTPError:
            log.warning("Entra token proxy unavailable")
            return JSONResponse(
                {"error": "temporarily_unavailable"},
                status_code=503,
                headers={"cache-control": "no-store", "pragma": "no-cache"},
            )
        headers = {
            "content-type": upstream.headers.get("content-type", "application/json"),
            "cache-control": upstream.headers.get("cache-control", "no-store"),
            "pragma": upstream.headers.get("pragma", "no-cache"),
        }
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            headers=headers,
        )

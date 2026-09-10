from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest
from mcp.server.auth.provider import AccessToken

from server import entra_auth


def _set_valid_entra_env(monkeypatch, **overrides: str) -> None:
    env = {
        "AZURE_TENANT_ID": "cbd1a264-94b1-4d60-b0f6-ca149e7aef80",
        "AZURE_CLIENT_ID": "11111111-1111-1111-1111-111111111111",
        "MCP_BASE_URL": "https://icl.example",
    }
    env.update(overrides)
    for key, value in env.items():
        monkeypatch.setenv(key, value)


def test_canonical_env_wins_over_legacy_alias(monkeypatch):
    from server.auth_env import canonical_or_legacy

    monkeypatch.setenv("AZURE_CLIENT_ID", "canonical")
    monkeypatch.setenv("KI_ICL_ENTRA_CLIENT_ID", "legacy")
    assert canonical_or_legacy("AZURE_CLIENT_ID", "KI_ICL_ENTRA_CLIENT_ID") == "canonical"


def test_entra_config_reads_canonical_names(monkeypatch):
    _set_valid_entra_env(monkeypatch)
    config = entra_auth.entra_config_from_env()
    assert config.tenant_id == "cbd1a264-94b1-4d60-b0f6-ca149e7aef80"
    assert config.client_id == "11111111-1111-1111-1111-111111111111"
    assert config.base_url == "https://icl.example"
    assert config.required_scope == "context.read"
    assert config.scope == "api://11111111-1111-1111-1111-111111111111/context.read"
    assert config.authorization_server == "https://icl.example/"
    assert "11111111-1111-1111-1111-111111111111" in config.audiences
    assert "api://11111111-1111-1111-1111-111111111111" in config.audiences


def test_entra_config_accepts_legacy_aliases(monkeypatch):
    monkeypatch.delenv("AZURE_TENANT_ID", raising=False)
    monkeypatch.delenv("AZURE_CLIENT_ID", raising=False)
    monkeypatch.delenv("MCP_BASE_URL", raising=False)
    monkeypatch.setenv("KI_ICL_ENTRA_TENANT_ID", "cbd1a264-94b1-4d60-b0f6-ca149e7aef80")
    monkeypatch.setenv("KI_ICL_ENTRA_CLIENT_ID", "11111111-1111-1111-1111-111111111111")
    monkeypatch.setenv("KI_ICL_ENTRA_BASE_URL", "https://icl.example")
    config = entra_auth.entra_config_from_env()
    assert config.client_id == "11111111-1111-1111-1111-111111111111"


def test_a_non_guid_tenant_is_refused(monkeypatch):
    _set_valid_entra_env(monkeypatch, AZURE_TENANT_ID="x/../../evil")
    with pytest.raises(SystemExit, match="TENANT"):
        entra_auth.entra_config_from_env()


def test_base_url_must_be_an_origin(monkeypatch):
    _set_valid_entra_env(monkeypatch, MCP_BASE_URL="https://icl.example/mcp")
    with pytest.raises(SystemExit, match="origin"):
        entra_auth.entra_config_from_env()


def test_full_uri_scopes_are_normalized_to_the_short_name():
    verifier = entra_auth.EntraJWTVerifier(
        jwks_uri="https://example.invalid/keys",
        issuer="https://example.invalid",
        audience="api://app",
        required_scopes=["context.read"],
        scope_resource="api://app",
    )
    assert "context.read" in verifier._extract_scopes(
        {"scp": "api://app/context.read"}
    )


def test_an_unrelated_uri_scope_is_not_shortened_to_context_read():
    verifier = entra_auth.EntraJWTVerifier(
        jwks_uri="https://example.invalid/keys",
        issuer="https://example.invalid",
        audience="api://app",
        required_scopes=["context.read"],
        scope_resource="api://app",
    )
    assert "context.read" not in verifier._extract_scopes(
        {"scp": "api://other/context.read"}
    )


def test_a_missing_client_id_is_refused(monkeypatch):
    _set_valid_entra_env(monkeypatch)
    monkeypatch.delenv("AZURE_CLIENT_ID", raising=False)
    monkeypatch.delenv("KI_ICL_ENTRA_CLIENT_ID", raising=False)
    with pytest.raises(SystemExit):
        entra_auth.entra_config_from_env()


def test_a_missing_base_url_is_refused(monkeypatch):
    _set_valid_entra_env(monkeypatch)
    monkeypatch.delenv("MCP_BASE_URL", raising=False)
    monkeypatch.delenv("KI_ICL_ENTRA_BASE_URL", raising=False)
    with pytest.raises(SystemExit):
        entra_auth.entra_config_from_env()


def test_a_base_url_with_a_query_is_refused(monkeypatch):
    _set_valid_entra_env(monkeypatch, MCP_BASE_URL="https://icl.example?next=/mcp")
    with pytest.raises(SystemExit, match="origin"):
        entra_auth.entra_config_from_env()


def test_a_base_url_with_a_fragment_is_refused(monkeypatch):
    _set_valid_entra_env(monkeypatch, MCP_BASE_URL="https://icl.example#frag")
    with pytest.raises(SystemExit, match="origin"):
        entra_auth.entra_config_from_env()


def test_azure_audience_is_the_only_audience(monkeypatch):
    _set_valid_entra_env(
        monkeypatch,
        AZURE_AUDIENCE="api://custom-app",
    )
    config = entra_auth.entra_config_from_env()
    assert config.audiences == ["api://custom-app"]
    assert config.scope == "api://custom-app/context.read"
    assert config.scope_resource == "api://custom-app"


def test_azure_scope_must_use_an_accepted_audience(monkeypatch):
    _set_valid_entra_env(
        monkeypatch,
        AZURE_SCOPE="api://other/context.read",
    )
    with pytest.raises(SystemExit):
        entra_auth.entra_config_from_env()


def test_entra_config_urls_follow_the_entra_v2_shapes(monkeypatch):
    _set_valid_entra_env(monkeypatch, MCP_BASE_URL="https://icl.example/")
    config = entra_auth.entra_config_from_env()
    tenant = "cbd1a264-94b1-4d60-b0f6-ca149e7aef80"
    assert config.issuer == f"https://login.microsoftonline.com/{tenant}/v2.0"
    assert config.jwks_uri == (
        f"https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys"
    )
    assert config.authorize_url == (
        f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize"
    )
    assert config.token_url == (
        f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    )
    assert config.authorization_server == "https://icl.example/"
    assert config.default_authorize_scope == (
        "openid offline_access "
        "api://11111111-1111-1111-1111-111111111111/context.read"
    )
    assert config.scope_resource == "api://11111111-1111-1111-1111-111111111111"


async def test_a_wrong_tenant_token_is_rejected_without_logging_claims(
    monkeypatch, caplog
):
    verifier = entra_auth.EntraJWTVerifier(
        jwks_uri="https://example.invalid/keys",
        issuer="https://example.invalid",
        audience="api://app",
        required_scopes=["context.read"],
        scope_resource="api://app",
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    )
    leaked = {
        "tid": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        "oid": "22222222-2222-2222-2222-222222222222",
        "email": "someone@kigroup.de",
        "preferred_username": "someone@kigroup.de",
    }
    parent_token = AccessToken(
        token="secret.jwt.token",
        client_id="app",
        scopes=["context.read"],
        claims=leaked,
    )
    monkeypatch.setattr(
        entra_auth.JWTVerifier,
        "load_access_token",
        AsyncMock(return_value=parent_token),
    )

    with caplog.at_level(logging.INFO):
        result = await verifier.load_access_token("secret.jwt.token")

    assert result is None
    assert "Entra bearer token rejected" in caplog.text
    assert "22222222-2222-2222-2222-222222222222" not in caplog.text
    assert "someone@kigroup.de" not in caplog.text
    assert "secret.jwt.token" not in caplog.text
    assert "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb" not in caplog.text


def test_protected_resource_metadata_names_this_host(monkeypatch):
    _set_valid_entra_env(monkeypatch)
    config = entra_auth.entra_config_from_env()
    meta = entra_auth.protected_resource_metadata(config)
    assert meta["resource"] == "https://icl.example/mcp"
    assert meta["authorization_servers"] == ["https://icl.example/"]
    assert meta["scopes_supported"] == [config.scope]


def test_oauth_metadata_points_authorize_and_token_at_this_host(monkeypatch):
    _set_valid_entra_env(monkeypatch)
    config = entra_auth.entra_config_from_env()
    meta = entra_auth.oauth_metadata(config)
    assert meta["authorization_endpoint"] == "https://icl.example/authorize"
    assert meta["token_endpoint"] == "https://icl.example/token"
    assert "login.microsoftonline.com" not in meta["issuer"]

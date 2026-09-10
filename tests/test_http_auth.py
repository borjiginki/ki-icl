"""Authentication over the real HTTP stack: what gets in, what gets a 401, what is logged.

There was no HTTP-transport test in this repo before, and auth is close to meaningless
without one: everything else in the suite drives the server in-process, where there is
no ASGI layer and so no `RequireAuthMiddleware` and no 401. These run the real Starlette
app through `httpx.ASGITransport`, so the authentication middleware, the transport
contextvar and the WWW-Authenticate header are all the production ones.

The negative cases go through `raw_http` rather than an MCP client on purpose. A single
`initialize` POST is enough to assert the status and read the header, with no handshake
to unwrap an exception out of, and the header contents are part of the contract.
"""

from __future__ import annotations

import pytest

from tests.conftest import INITIALIZE


async def test_a_valid_token_reaches_the_tools(auth_app, over_http):
    app = auth_app("demo")

    async with over_http(app, token="demo-token-broad") as client:
        names = {t.name for t in await client.list_tools()}

    assert "list_domains" in names


async def test_no_authorization_header_is_rejected_before_any_tool_runs(
    auth_app, raw_http, audit
):
    """The load-bearing assertion is the second one. It is what proves fail-closed
    happens at the transport rather than inside a tool: a 401 with a usage record would
    mean something ran and looked at the corpus first.
    """
    app = auth_app("demo")

    async with raw_http(app) as client:
        response = await client.post("/mcp", json=INITIALIZE)

    assert response.status_code == 401
    records, _ = audit()
    assert not [r for r in records if r.get("event") == "context_use"]


async def test_the_rejection_tells_the_client_where_to_go_and_authenticate(auth_app, raw_http):
    """RFC 9728. Without `resource_metadata` a client has nothing to discover and the
    OAuth flow cannot start, so this header is the difference between "sign in" and
    "broken"."""
    app = auth_app("demo")

    async with raw_http(app) as client:
        response = await client.post("/mcp", json=INITIALIZE)

    assert "resource_metadata=" in response.headers.get("www-authenticate", "")


async def test_an_expired_token_is_rejected(auth_app, raw_http):
    """The demo table keeps a row with a dead `expires_at`, so this needs no RSA key and
    no clock manipulation."""
    app = auth_app("demo")

    async with raw_http(app, token="demo-token-expired") as client:
        response = await client.post("/mcp", json=INITIALIZE)

    assert response.status_code == 401


async def test_an_unknown_token_is_rejected(auth_app, raw_http):
    app = auth_app("demo")

    async with raw_http(app, token="demo-token-invented") as client:
        response = await client.post("/mcp", json=INITIALIZE)

    assert response.status_code == 401


async def test_a_garbage_token_is_rejected_rather_than_crashing(auth_app, raw_http):
    app = auth_app("demo")

    async with raw_http(app, token="not.a.token") as client:
        response = await client.post("/mcp", json=INITIALIZE)

    assert response.status_code == 401


async def test_with_auth_off_there_is_no_gate_at_all(auth_app, over_http):
    """`off` is a real mode, and its behaviour has to be tested rather than assumed:
    it is what stdio and local development run as."""
    app = auth_app("off")

    async with over_http(app) as client:
        names = {t.name for t in await client.list_tools()}

    assert "list_domains" in names


# --- what the log says about the caller -------------------------------------


async def test_the_log_names_the_caller_as_a_pseudonym(auth_app, over_http, audit):
    app = auth_app("demo")

    async with over_http(app, token="demo-token-broad") as client:
        await client.call_tool("list_domains", {})

    records, _ = audit()
    used = [r for r in records if r.get("event") == "context_use"]
    assert used, records
    assert used[0]["actor"].startswith("p_")
    assert used[0]["actor_key"].startswith("k_")


async def test_two_different_callers_get_two_different_pseudonyms(auth_app, over_http, audit):
    app = auth_app("demo")

    for token in ("demo-token-broad", "demo-token-baseline"):
        async with over_http(app, token=token) as client:
            await client.call_tool("list_domains", {})

    records, _ = audit()
    actors = {r["actor"] for r in records if r.get("event") == "context_use"}
    assert len(actors) == 2, records


async def test_the_same_caller_twice_gets_one_stable_pseudonym(auth_app, over_http, audit):
    app = auth_app("demo")

    for _ in range(2):
        async with over_http(app, token="demo-token-broad") as client:
            await client.call_tool("list_domains", {})

    records, _ = audit()
    actors = {r["actor"] for r in records if r.get("event") == "context_use"}
    assert len(actors) == 1, records


async def test_the_roles_are_recorded_once_per_connection_and_not_on_every_line(
    auth_app, over_http, audit
):
    """Data minimisation, and it produces the "this person connected holding these
    rights" record an access review wants, which per-line repetition does not give as
    cleanly."""
    app = auth_app("demo")

    async with over_http(app, token="demo-token-delivery") as client:
        await client.call_tool("list_domains", {})
        await client.call_tool("get_domain_manifest", {"domain": "company"})

    records, _ = audit()
    sessions = [r for r in records if r.get("event") == "access_session"]
    used = [r for r in records if r.get("event") == "context_use"]

    assert len(sessions) == 1, records
    assert "ctx.delivery" in sessions[0]["roles"]
    assert len(used) >= 2
    assert all("roles" not in r for r in used)


async def test_a_gap_report_is_attributed_too(auth_app, over_http, audit):
    """`report_gap` writes from the tool body rather than through the middleware, so it
    is the one path that could quietly lose the actor."""
    app = auth_app("demo")

    async with over_http(app, token="demo-token-broad") as client:
        await client.call_tool("report_gap", {"domain": "hr", "topic": "parental-leave"})

    records, _ = audit()
    (gap,) = [r for r in records if r.get("event") == "context_gap"]
    assert gap["actor"].startswith("p_")


# --- the absences -----------------------------------------------------------


async def test_the_raw_token_never_appears_in_the_log(auth_app, over_http, audit):
    """Each dot-separated segment as well as the whole string: somebody logging
    `token.split(".")[1]` to debug a claim would leak the entire claim set including the
    UPN, and a whole-string assertion would pass while that happened."""
    token = "demo-token-broad"
    app = auth_app("demo")

    async with over_http(app, token=token) as client:
        await client.call_tool("list_domains", {})

    _, text = audit()
    assert token not in text
    for segment in token.split("."):
        assert segment not in text


async def test_the_log_never_contains_the_object_id(auth_app, over_http, audit):
    from server import demo_principals

    oid = demo_principals.load(demo_principals.DEFAULT_TABLE)["demo-token-broad"]["oid"]
    app = auth_app("demo")

    async with over_http(app, token="demo-token-broad") as client:
        await client.call_tool("list_domains", {})
        await client.call_tool("get_domain_manifest", {"domain": "company"})

    _, text = audit()
    assert oid not in text


async def test_an_unkeyed_deployment_records_no_actor_at_all(
    auth_app, over_http, audit, monkeypatch
):
    """Absent, not null and not "unknown": an absent key cannot be aggregated by
    accident, whereas a placeholder becomes a bucket that reads as one busy person."""
    from server import identity

    monkeypatch.setattr(identity, "_AUDIT_KEY", None)
    app = auth_app("demo")

    async with over_http(app, token="demo-token-broad") as client:
        await client.call_tool("list_domains", {})

    records, _ = audit()
    used = [r for r in records if r.get("event") == "context_use"]
    assert used, records
    assert all("actor" not in r and "actor_key" not in r for r in used)


async def test_an_unkeyed_deployment_still_serves(auth_app, over_http, monkeypatch):
    """Neither authentication nor authorization may depend on the audit key. A missing
    secret degrades the log; it must never open or close the door."""
    from server import identity

    monkeypatch.setattr(identity, "_AUDIT_KEY", None)
    app = auth_app("demo")

    async with over_http(app, token="demo-token-broad") as client:
        result = await client.call_tool("list_domains", {})

    assert result.content


# --- the startup gates ------------------------------------------------------


@pytest.fixture
def served(catalog, monkeypatch):
    """Point the server at a tree whose policy loads.

    The startup gate refuses to boot on an unusable policy, so a test about the auth
    mode has to stand on a valid tree or it fails for the wrong reason.
    """
    from server import artifacts

    monkeypatch.setattr(artifacts, "ARTIFACTS_ROOT", catalog)
    return catalog


def test_serving_http_without_saying_which_auth_mode_is_refused(served, monkeypatch):
    """A forgotten variable must be a server that does not come up, not one that serves
    HR content to anyone who can reach the port."""
    from server import mcp_server

    monkeypatch.delenv("KI_ICL_AUTH", raising=False)

    with pytest.raises(SystemExit, match="KI_ICL_AUTH"):
        mcp_server.refuse_unsafe_start(http=True, host="127.0.0.1")


def test_serving_unauthenticated_over_http_is_allowed_but_only_by_saying_so(served, monkeypatch):
    """Typing `off` IS the escape hatch. It shows up in the process listing and on every
    server_start record, which beats a second flag nobody reads."""
    from server import mcp_server

    monkeypatch.setenv("KI_ICL_AUTH", "off")

    mcp_server.refuse_unsafe_start(http=True, host="127.0.0.1")


def test_stdio_needs_no_auth_mode(served, monkeypatch):
    """There is no token under stdio, so demanding a mode there would break the local
    client config for nothing."""
    from server import mcp_server

    monkeypatch.delenv("KI_ICL_AUTH", raising=False)

    mcp_server.refuse_unsafe_start(http=False, host="127.0.0.1")


def test_the_dev_principal_is_refused_in_entra_mode(served, monkeypatch):
    from server import mcp_server

    monkeypatch.setenv("KI_ICL_AUTH", "entra")
    monkeypatch.setenv("KI_ICL_DEV_PRINCIPAL", "broad")

    with pytest.raises(SystemExit, match="DEV_PRINCIPAL"):
        mcp_server.refuse_unsafe_start(http=True, host="127.0.0.1")


def test_demo_tokens_refuse_to_bind_anywhere_but_loopback(served, monkeypatch):
    from server import mcp_server

    monkeypatch.setenv("KI_ICL_AUTH", "demo")

    with pytest.raises(SystemExit, match="loopback"):
        mcp_server.refuse_unsafe_start(http=True, host="0.0.0.0")


def test_entra_mode_refuses_to_start_unlogged_when_logging_is_a_declared_control(served, monkeypatch):
    from server import mcp_server

    monkeypatch.setenv("KI_ICL_AUTH", "entra")
    monkeypatch.setenv("KI_ICL_AUDIT_REQUIRED", "1")
    monkeypatch.delenv("KI_ICL_AUDIT_KEY", raising=False)

    with pytest.raises(SystemExit, match="AUDIT"):
        mcp_server.refuse_unsafe_start(http=True, host="127.0.0.1")


def test_a_tenant_id_that_is_not_a_guid_is_refused_before_it_reaches_a_url(monkeypatch):
    """An unvalidated tenant id is a path injection into the JWKS URL, which is the key
    source this server trusts to validate every token."""
    from server import mcp_server

    monkeypatch.setenv("KI_ICL_AUTH", "entra")
    monkeypatch.setenv("AZURE_TENANT_ID", "x/../../evil")
    monkeypatch.setenv("KI_ICL_ENTRA_TENANT_ID", "x/../../evil")
    monkeypatch.setenv("KI_ICL_ENTRA_CLIENT_ID", "cid")
    monkeypatch.setenv("KI_ICL_ENTRA_BASE_URL", "https://example.invalid")

    with pytest.raises(SystemExit, match="TENANT"):
        mcp_server.auth_from_env()


def test_entra_mode_needs_no_client_secret(monkeypatch):
    """The reason RemoteAuthProvider was chosen over AzureProvider: a pure resource
    server holds a public JWKS URL and nothing else, so there is no secret to store, to
    rotate, or to leak. If this ever starts needing one, something has changed."""
    import inspect

    from server import mcp_server

    source = inspect.getsource(mcp_server._entra_auth)

    assert "client_secret" not in source
    assert "SECRET" not in source


def test_a_tree_with_no_usable_policy_refuses_to_start(monkeypatch, tmp_path):
    """A policy that fails to load degrades to deny-all at runtime, which is right for a
    file that breaks after deployment and wrong to boot into: every read refused, with
    the cause visible only in a log warning nobody is watching yet."""
    from server import artifacts, mcp_server

    monkeypatch.setattr(artifacts, "ARTIFACTS_ROOT", tmp_path / "no-policy-here")
    monkeypatch.setenv("KI_ICL_AUTH", "off")

    with pytest.raises(SystemExit, match="access-policy.yaml"):
        mcp_server.refuse_unsafe_start(http=True, host="127.0.0.1")


def test_identity_does_not_import_the_composition_root():
    """`identity` resolving a dev principal must not reach back through `mcp_server`,
    which imports it: the cycle works only by luck of deferred-import timing."""
    import inspect

    from server import identity

    source = inspect.getsource(identity)

    assert "server.mcp_server" not in source
    assert "from server import mcp_server" not in source


# --- where it listens -------------------------------------------------------


def test_the_bind_address_is_loopback_unless_asked_otherwise(monkeypatch):
    """A container has to ask for a wider bind. Defaulting to 0.0.0.0 would mean a
    careless local run exposes the corpus to the network."""
    from server import mcp_server

    monkeypatch.delenv("KI_ICL_HOST", raising=False)
    monkeypatch.delenv("KI_ICL_PORT", raising=False)

    assert mcp_server.bind_address() == ("127.0.0.1", 8000)


def test_a_container_can_bind_all_interfaces(monkeypatch):
    """Container Apps routes to the container's own address, so it must listen on
    0.0.0.0. The network boundary there is the internal ingress, not the bind."""
    from server import mcp_server

    monkeypatch.setenv("KI_ICL_HOST", "0.0.0.0")
    monkeypatch.setenv("KI_ICL_PORT", "8080")

    assert mcp_server.bind_address() == ("0.0.0.0", 8080)


def test_a_port_that_is_not_a_number_is_refused_rather_than_defaulted(monkeypatch):
    from server import mcp_server

    monkeypatch.setenv("KI_ICL_PORT", "eight-thousand")

    with pytest.raises(SystemExit, match="KI_ICL_PORT"):
        mcp_server.bind_address()


def test_demo_identities_still_refuse_a_wide_bind(served, monkeypatch):
    """The guard that matters once the bind is configurable: fake identities must never
    be reachable by anything but this machine."""
    from server import mcp_server

    monkeypatch.setenv("KI_ICL_AUTH", "demo")

    with pytest.raises(SystemExit, match="loopback"):
        mcp_server.refuse_unsafe_start(http=True, host="0.0.0.0")


def test_serving_unauthenticated_off_loopback_is_announced_loudly(monkeypatch):
    """`off` is a deliberate declaration, and on a wide bind it means the corpus is
    readable by anything that can reach the port. The banner has to say so, because the
    server cannot know whether a VNet is the boundary."""
    from server import access, mcp_server

    monkeypatch.setenv("KI_ICL_AUTH", "off")

    lines = mcp_server.startup_lines(
        host="0.0.0.0", port=8000, http=True, mode=access.Mode.OBSERVE
    )
    text = " ".join(lines)

    assert "UNAUTHENTICATED" in text
    assert "0.0.0.0" in text


def test_a_loopback_run_is_not_shouted_at(monkeypatch):
    """The warning has to be absent when it does not apply, or it becomes noise and
    stops being read. Same argument as the review caveat."""
    from server import access, mcp_server

    monkeypatch.setenv("KI_ICL_AUTH", "off")

    lines = mcp_server.startup_lines(
        host="127.0.0.1", port=8000, http=True, mode=access.Mode.OBSERVE
    )

    assert "UNAUTHENTICATED" not in " ".join(lines)

"""Shared fixtures: a throwaway artifacts catalog on tmp_path."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# domains/ and access-policy.yaml moved to ki-ccl (github.com/ki-group-gmbh/ki-ccl).
# A few tests still want the *real* shipped policy, not a fixture, to prove the fail-
# closed authorization model holds against real data rather than only against
# synthetic cases - see test_access.py and test_demo_principals.py. Those tests read
# it from a sibling checkout, which local dev already needs for CONTEXT_ROOT (see the
# Makefile), and which ci.yml checks out alongside this repo for exactly this reason.
# Skip rather than fail when it is absent, so this repo's suite still runs standalone.
CCL_ROOT = Path(os.environ.get("KI_CCL_ROOT", REPO_ROOT.parent / "ki-ccl"))


def load_real_policy():
    """The real access-policy.yaml from the sibling ki-ccl checkout, or a skip."""
    from server import access

    if not (CCL_ROOT / "access-policy.yaml").is_file():
        pytest.skip(f"no ki-ccl checkout at {CCL_ROOT} (set KI_CCL_ROOT to override)")
    return access.load_policy(CCL_ROOT, mode=access.Mode.ENFORCE)


def write_artifact(domain_dir: Path, artifact_id: str, **files: str) -> None:
    """Create <domain_dir>/<artifact_id>/ holding `files` (name -> body)."""
    d = domain_dir / artifact_id
    d.mkdir(parents=True)
    for name, body in files.items():
        path = d / name.replace("__", ".")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")


# Every KNOWN_DOMAIN appears, because the gate requires the policy to decide all of
# them whether or not content exists there yet. Tests that check one policy rule
# overwrite this with their own single mutation.
VALID_POLICY = """\
version: 1
roles:
  ctx.colleague:
    description: Any authenticated colleague.
    grants:
      company: internal
      method: internal
      offerings: internal
      case-studies: internal
      team: internal
      marketing: internal
      projects: internal
  ctx.people:
    description: HR and the works-council contact.
    grants:
      team: confidential
"""


def write_policy(root: Path, body: str = VALID_POLICY) -> Path:
    """Put an access policy at the root of a tree. Returns the root."""
    from server.access import POLICY_FILENAME

    (root / POLICY_FILENAME).write_text(body, encoding="utf-8")
    return root


def as_colleague() -> dict:
    """One permissive reader, for tests about the read path rather than authorization.

    Every payload function requires `principal` and `policy`, keyword-only and with no
    default, so that no read path can be written that forgets who is asking. Tests that
    are not about that decision splat this: `ctx.colleague`, which the fixtures' own
    policy grants every domain at `internal`. The authorization behaviour itself is in
    `tests/test_scoping.py`, and it builds its principals explicitly.
    """
    from server import access

    return {
        "principal": access.Principal(
            authenticated=True,
            roles=frozenset({"ctx.colleague"}),
            actor=None,
            actor_key=None,
            tenant=None,
            client=None,
            source="demo",
        ),
        "policy": access.parse_policy(yaml.safe_load(VALID_POLICY), mode=access.Mode.ENFORCE),
    }


AS_COLLEAGUE = as_colleague()


@pytest.fixture
def catalog(tmp_path: Path) -> Path:
    """A packaged catalog root: <root>/domains/company/ with two artifacts."""
    company = tmp_path / "domains" / "company"
    company.mkdir(parents=True)

    write_artifact(
        company,
        "expense-policy",
        README__md="# Expense policy\n\nReceipts within 30 days.\n",
        artifact__yaml="title: Expense policy\n",
    )
    write_artifact(
        company,
        "discovery-workshop",
        README__md="# Discovery workshop\n",
        artifact__yaml="title: Discovery workshop\n",
    )

    (company / "_manifest.json").write_text(
        json.dumps(
            {
                "domain": "company",
                "description": "How we work and what we offer.",
                "owner": None,
                "artifacts": [
                    {
                        "id": "discovery-workshop",
                        "title": "Discovery workshop",
                        "kind": "methodology",
                        "review": "draft",
                        "sensitivity": "internal",
                        "description": "How we run discovery.",
                        "class": "functional",
                        "owner": None,
                        "version_id": "bbbb2222",
                    },
                    {
                        "id": "expense-policy",
                        "title": "Expense policy",
                        "kind": "guideline",
                        "review": "draft",
                        "sensitivity": "internal",
                        "description": "What we reimburse.",
                        "class": None,
                        "owner": None,
                        "version_id": "aaaa1111",
                    },
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    # A packaged tree carries its grant table at the root, so one without a policy is
    # not a realistic catalog: the loader would deny every read and every test would be
    # exercising the deny-all path by accident.
    write_policy(tmp_path)
    return tmp_path


@pytest.fixture
def artifacts(catalog: Path, monkeypatch: pytest.MonkeyPatch):
    """The read-path module, pointed at the `catalog` fixture."""
    from server import artifacts as module

    monkeypatch.setattr(module, "ARTIFACTS_ROOT", catalog)
    return module


@pytest.fixture(autouse=True)
def clean_auth_env(monkeypatch):
    """No KI_ICL_* variable from the developer's shell may change a test's outcome.

    Autouse for the same reason `isolated_usage_log` is: remembering to opt in is the
    thing that fails silently, and here the failure would be a suite that passes on one
    machine and denies everything on another. The audit key is patched onto the module
    rather than the environment because `identity` reads it once at import.
    """
    from server import identity

    for name in (
        "KI_ICL_AUTH",
        "KI_ICL_ENFORCE",
        "KI_ICL_OBSERVE_UNTIL",
        "KI_ICL_AUDIT_KEY",
        "KI_ICL_AUDIT_REQUIRED",
        "KI_ICL_DEV_PRINCIPAL",
        "KI_ICL_ENTRA_TENANT_ID",
        "KI_ICL_ENTRA_CLIENT_ID",
        "KI_ICL_ENTRA_BASE_URL",
        "KI_ICL_ENTRA_IDENTIFIER_URI",
        "AZURE_TENANT_ID",
        "AZURE_CLIENT_ID",
        "AZURE_AUDIENCE",
        "AZURE_SCOPE",
        "MCP_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(identity, "_AUDIT_KEY", b"test-audit-key-0123456789abcdef0123")
    monkeypatch.setattr(identity, "_EXPECTED_TENANT", None)


@pytest.fixture
def audit(tmp_path: Path, monkeypatch):
    """Read back what was logged. Returns a callable giving (records, raw text).

    The raw text matters as much as the records: the strongest guarantees in this suite
    are absences, and a substring assertion over the whole file catches a leak in a
    field nobody thought to check.
    """
    from server import usage

    path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(usage, "USAGE_LOG", usage.UsageLog(path=path, stream=None))

    def read() -> tuple[list[dict], str]:
        if not path.exists():
            return [], ""
        text = path.read_text(encoding="utf-8")
        return [json.loads(line) for line in text.splitlines() if line.strip()], text

    return read


@pytest.fixture
def auth_app(catalog: Path, monkeypatch):
    """Build the real ASGI app under a chosen auth mode. A factory, not an app.

    `auth=` is constructor-only in FastMCP, so each configuration needs its own server,
    which is what `build_server()` exists for. Call this once per test: two `http_app()`
    calls create two session managers.
    """
    from server import artifacts, mcp_server

    monkeypatch.setattr(artifacts, "ARTIFACTS_ROOT", catalog)

    def build(mode: str = "demo", **env: str):
        monkeypatch.setenv("KI_ICL_AUTH", mode)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        server = mcp_server.build_server(auth=mcp_server.auth_from_env())
        return server.http_app(path="/mcp")

    return build


@pytest.fixture
def over_http():
    """An MCP client speaking to an ASGI app in-process, optionally bearing a token.

    In-memory rather than a live uvicorn port: the real Starlette stack runs, including
    the authentication and RequireAuth middleware and the transport contextvar, with no
    port to allocate, no sleep, and nothing to go flaky in CI.

    The lifespan has to be entered by hand. The streamable-HTTP session manager is
    created there, and ASGITransport does not run it.
    """
    import contextlib

    import httpx
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport

    @contextlib.asynccontextmanager
    async def connect(app, token: str | None = None):
        def factory(**kwargs):
            kwargs.pop("transport", None)
            kwargs.pop("base_url", None)
            return httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver", **kwargs
            )

        transport = StreamableHttpTransport(
            url="http://testserver/mcp",
            headers={"Authorization": f"Bearer {token}"} if token else None,
            httpx_client_factory=factory,
        )
        async with app.router.lifespan_context(app):
            async with Client(transport) as client:
                yield client

    return connect


@pytest.fixture
def raw_http():
    """A bare HTTP client, for the negative cases.

    One POST with an `initialize` body is enough to assert a 401 and inspect the
    WWW-Authenticate header, with no MCP handshake to unwrap an exception out of.
    """
    import contextlib

    import httpx

    @contextlib.asynccontextmanager
    async def connect(app, token: str | None = None):
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
                headers=headers,
            ) as client:
                yield client

    return connect


INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}


@pytest.fixture(autouse=True)
def isolated_usage_log(tmp_path: Path, monkeypatch):
    """No test may append to the repo's real logs/usage.jsonl.

    The middleware is registered on the module-level `mcp`, so any test that calls a
    tool writes through it. Autouse, because remembering to opt in is exactly the
    thing that fails silently.
    """
    from server import usage

    monkeypatch.setattr(
        usage, "USAGE_LOG", usage.UsageLog(path=tmp_path / "usage.jsonl", stream=None)
    )

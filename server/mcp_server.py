#!/usr/bin/env python3
"""Throwaway demo harness: the four context-layer tools over stdio or HTTP.

This file exists so the read path can be driven end to end from a real MCP client
today. In production these tools live in ki-mcp as `server/tools/artifact_tools.py`,
registered on the shared `mcp` instance beside the skills tools. Only the import of
`mcp` changes; the four function bodies move verbatim.

    CONTEXT_ROOT=dist/staging python3 server/mcp_server.py                  # stdio
    KI_ICL_AUTH=demo CONTEXT_ROOT=dist/staging python3 server/mcp_server.py --http

**The server is built by a factory rather than at import time**, because FastMCP takes
`auth=` only in the constructor. Without `build_server()` there would be no way to
exercise more than one auth configuration in one process, and the HTTP auth tests could
not exist. `mcp` is still bound at module level, because `scripts/demo.py` and four of
the test modules import that name. `scripts/dashboard.py` does not: it is a host for
`server/dashboard.py` and imports nothing from here.

## Authentication is HTTP-only, and that is honest rather than a gap

Under stdio the client spawns this process, owns its stdin, and runs it as the invoking
user; FastMCP itself skips auth entirely when the transport is stdio. A bearer token
there would prove nothing that OS process ownership has not already decided. So
`KI_ICL_AUTH` is unset for stdio, which is how the local client config already looks,
and nothing about local development changes.

Over HTTP the server is a pure OAuth **resource server**: `RemoteAuthProvider` wrapping
`JWTVerifier`, holding a public JWKS URL and no secret of any kind. Claude authenticates
against Entra and this server only ever verifies the result. `AzureProvider` was
rejected deliberately: it is an `OAuthProxy`, so it would need a confidential-client
secret to store and rotate, would make this a second token issuer with its own
lifetimes and revocation to own, and would add a client-registration store with its own
retention question. All of that is machinery for being an OAuth *client* toward Entra,
which is not what this is.

One consequence to keep in mind when adding tools: authentication cannot be
observational. The moment `auth=` is non-None, FastMCP wraps `/mcp` in
`RequireAuthMiddleware`, so a bad token is a 401 in the ASGI layer before any middleware
or tool body runs. Only *authorization* has an observe mode.

Do not use component-level `@mcp.tool(auth=...)`. FastMCP's transport contextvar is
unset for in-memory calls, so `mcp.call_tool()` and in-memory `Client(mcp)` count as
"auth enforced, no token" and every existing test would deny.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

# Run directly (`python3 server/mcp_server.py`) and only server/ lands on sys.path,
# so the repo root has to be put there before `server.artifacts` can be imported.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastmcp import FastMCP  # noqa: E402
from fastmcp.server.auth import AuthProvider  # noqa: E402
from starlette.requests import Request  # noqa: E402
from starlette.responses import JSONResponse  # noqa: E402

from server import access  # noqa: E402
from server import artifacts  # noqa: E402
from server import entra_auth  # noqa: E402
from server import identity  # noqa: E402
# The module rather than `from ... import register`, for the same reason as usage
# below: startup_lines prints dashboard.LOG, and a path imported by value here would
# not follow the one the dashboard actually reads.
from server import dashboard as usage_dashboard  # noqa: E402

# The module, not `from ... import USAGE_LOG`: importing the sink by value creates a
# second binding that silently diverges from the one the middleware writes through.
from server import usage  # noqa: E402
from server.usage import ContextUsageMiddleware  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent

INSTRUCTIONS = (
    "Company information lives in domains and is fetched by id, never guessed. "
    "Start with `list_domains` (you need no prior knowledge), then "
    "`get_domain_manifest(domain)` to choose, then `get_artifact(domain, ids)`. "
    "Use these when the user asks what KI group does, offers, or requires, and "
    "also for the current state of its work: the `projects` domain holds one "
    "artifact per live engagement. A `not_found` means no artifact has that id: "
    "say so, and never substitute a similar one.\n\n"
    "Call `list_domains` for the question in front of you rather than trusting a "
    "listing from earlier in the conversation. The corpus is edited while you are "
    "talking, so a domain that held nothing an hour ago may hold the answer now, "
    "and reporting it as empty from memory is a wrong answer with no miss "
    "recorded anywhere.\n\n"
    "A manifest row may carry a `progress` object with `stage`, `health` and "
    "`as_of`. A question about several artifacts at once is answered from the "
    "manifest alone, with no fetch. Always state the `as_of` date in such an "
    "answer: `version_id` is opaque and cannot tell you how old an assessment "
    "is, so that date is the only recency signal there is.\n\n"
    "Every row carries `review`. When it is not `approved`, say so in the answer "
    "and say which: `demo` means the content is invented and exists only to "
    "exercise the pipeline, so it must never be repeated as fact about the "
    "company; `draft` means the subject is real but no owner has checked it. "
    "This matters most when you answer from the manifest without fetching, "
    "because a row reads as settled fact and nothing else in the payload will "
    "tell the user otherwise.\n\n"
    "What you can see depends on who the user is. A domain answering "
    "`status: forbidden` exists but is not readable by this user: tell them they do "
    "not have access to it, and do NOT call `report_gap` for it, because nothing is "
    "missing. Never list or guess at what such a domain might contain.\n\n"
    "If the manifest has no answer for what was asked, call "
    "`report_gap(domain, topic)` before you reply, so the gap is recorded and can "
    "be written up. Reading a manifest and finding nothing otherwise leaves no "
    "trace at all, and the corpus never learns what it is missing."
)


def reader() -> dict[str, Any]:
    """The two arguments every read path needs: who is asking, and the grant table.

    This is the composition root's job, and it is the only place the two are brought
    together: `server/access.py` reads no environment, so somebody has to supply the
    enforcement mode, and `server/artifacts.py` takes both as required arguments so no
    read path can be written that forgets them.

    The policy is loaded per call, exactly like `_manifest.json` is. It is a kilobyte,
    and "everything under the served root is fresh" is one mental model with no cache
    that could serve one caller's filtering to another.
    """
    return {
        "principal": identity.effective_principal(),
        "policy": access.load_policy(
            artifacts.ARTIFACTS_ROOT, mode=identity.effective_mode()
        ),
    }


def build_server(auth: AuthProvider | None = None, dashboard: bool = False) -> FastMCP:
    """One configured server. The only place the tools are registered.

    `mask_error_details=True` is not tidiness: FastMCP returns exception text to the
    client verbatim by default, so a traceback naming an artifact id would disclose
    exactly what authorization exists to withhold.

    `dashboard` mounts the usage dashboard at /dashboard, on this same app and port.
    A parameter rather than an environment read, for the same reason `auth` is one:
    this function reads no environment, the composition root decides, and a test can
    mount the routes without touching `os.environ`.
    """
    mcp = FastMCP(name="ki-icl", instructions=INSTRUCTIONS, auth=auth, mask_error_details=True)
    mcp.add_middleware(ContextUsageMiddleware())

    @mcp.tool
    def list_domains() -> str:
        """List every company-information domain. Start here; you need no prior knowledge.

        Only domains this user may read are listed, so the listing can differ between
        users. Do not carry one over from earlier in the conversation.
        """
        return json.dumps(artifacts.list_domains_payload(**reader()), indent=2)

    @mcp.tool
    def get_domain_manifest(domain: str) -> str:
        """List one domain's artifacts with descriptions, so you can choose what to fetch.

        Returns no file bodies. Read each `description` as a "when to use" signal, then
        fetch the ones you need with `get_artifact`. An unknown domain returns
        `status: not_found` along with the domains that do exist.

        `status: forbidden` is different and means the domain exists but this user may
        not read it. Tell the user they do not have access. Do NOT call `report_gap`
        for it: nothing is missing, and a gap recorded there is a false signal that
        somebody will act on. Do not guess at what it contains.

        Args:
            domain: Domain id, exactly as `list_domains` reported it.
        """
        return json.dumps(artifacts.domain_manifest_payload(domain, **reader()), indent=2)

    @mcp.tool
    def get_artifact(domain: str, ids: str | list[str], max_file_bytes: int = 1_048_576) -> str:
        """Return the full text of one or more artifacts, by exact id.

        Pass several ids to fetch them in one call. Each is answered independently: an id
        that does not exist comes back as `{"status": "not_found", "id": ...}` while the
        rest return normally.

        A `not_found` means no artifact has that id. Say so. Never substitute a similar
        one, and never answer from memory in its place. Every found artifact carries an
        opaque `version_id`; compare it for equality to detect staleness, never parse it.

        Args:
            domain: Domain id, from `list_domains`.
            ids: One artifact id, or a list of them, exactly as `get_domain_manifest` reported them.
            max_file_bytes: Per-file size cap (default 1 MiB). Larger files are listed in `skipped_files`.
        """
        payload = artifacts.get_artifact_payload(
            domain, ids, max_file_bytes=max_file_bytes, **reader()
        )
        return json.dumps(payload, indent=2)

    @mcp.tool
    def report_gap(domain: str, topic: str) -> str:
        """Record that the context layer had no answer, so the gap can be written up.

        Call this when you have read `get_domain_manifest` and nothing in it answers what
        the user asked. Without it that need is invisible: reading a manifest and finding
        nothing leaves no trace, so the corpus never learns what it is missing.

        Do NOT call it for a domain that answered `status: forbidden`. That domain is
        not missing anything; the user simply may not read it, and a gap recorded there
        is a false signal.

        Call it once per genuine gap, and only for things that plausibly belong in a
        company knowledge base: a policy, a methodology, an offering, a guideline. Do not
        report one-off trivia, anything specific to a single person or client, or a
        question the user could not reasonably expect the company to have documented.

        `topic` is a short kebab-case label naming the missing document, the way an
        artifact id would look, for example `parental-leave` or `onboarding`. It is NOT
        the user's question: never pass their words, their name, client details, or any
        sentence. Overlong or free-text topics are rejected.

        Args:
            domain: Domain id it would belong to, from `list_domains`. Your best guess is fine.
            topic: Short kebab-case label for the missing document, at most 5 words.
        """
        record = usage.gap_record(domain, topic)
        if record is None:
            return json.dumps(
                {
                    "status": "rejected",
                    "reason": (
                        "`topic` must be a short kebab-case label naming the missing "
                        "document (for example `parental-leave`), at most 5 words and 48 "
                        "characters. It is not the user's question. Nothing was recorded."
                    ),
                },
                indent=2,
            )
        usage.USAGE_LOG.write({**usage.current_correlation(), **record})
        return json.dumps(
            {
                "status": "recorded",
                "domain": record["domain"],
                "topic": record["topic"],
                "note": "Recorded as a gap. Tell the user it is not available; do not invent an answer.",
            },
            indent=2,
        )

    @mcp.custom_route("/health", methods=["GET"])
    async def _health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    if isinstance(auth, entra_auth.EntraAuthProvider):
        entra_auth.register_oauth_routes(mcp, auth.entra_config)

    if dashboard:
        usage_dashboard.register(mcp)

    return mcp


# --- auth wiring ------------------------------------------------------------

AUTH_MODES = ("entra", "demo", "off")


def auth_mode() -> str:
    """`entra`, `demo`, `off`, or "" when unset. Never guessed into something safer."""
    return os.environ.get("KI_ICL_AUTH", "").strip().lower()


def dashboard_from_env() -> bool:
    """Whether to mount the usage dashboard. Off unless explicitly switched on.

    Exactly "1", like KI_ICL_AUDIT_REQUIRED, rather than any truthy-looking string.
    This one decides whether an unauthenticated view of the whole corpus index is
    reachable, so "true", "yes" and "0 " should all fail closed rather than be guessed
    at.
    """
    return os.environ.get("KI_ICL_DASHBOARD", "").strip() == "1"


def _demo_auth() -> AuthProvider:
    """Fake tokens from `config/demo_principals.yaml`. Refuses a non-local table.

    Wrapped in `RemoteAuthProvider` rather than handed over bare, even though a static
    verifier needs no authorization server. A bare `TokenVerifier` publishes no routes,
    so a rejection would carry `WWW-Authenticate: Bearer` and nothing to discover, and
    demo mode would exercise a materially different path from the real one. Wrapping it
    means the RFC 9728 protected-resource document is served and tested here, which is
    the piece most likely to be misconfigured in production and the hardest to notice.
    """
    from fastmcp.server.auth import RemoteAuthProvider
    from fastmcp.server.auth.providers.jwt import StaticTokenVerifier

    from server import demo_principals

    return RemoteAuthProvider(
        token_verifier=StaticTokenVerifier(
            tokens=demo_principals.load(demo_principals.DEFAULT_TABLE)
        ),
        # A deliberately unreachable issuer: demo tokens are minted from a file, so
        # nothing should ever follow this, and a real URL here would invite somebody to.
        authorization_servers=["https://demo.invalid/v2.0"],
        base_url="http://127.0.0.1:8000",
        scopes_supported=["context.read"],
        resource_name="ki-icl (demo identities)",
    )


def _entra_auth() -> AuthProvider:
    """A pure resource server against KI group's Entra tenant. Holds no secret."""
    return entra_auth.EntraAuthProvider(entra_auth.entra_config_from_env())


def auth_from_env() -> AuthProvider | None:
    """The provider `KI_ICL_AUTH` selects. None for `off` and for unset.

    Unset returns None rather than raising, because stdio has no token to verify and
    must keep working. The `--http` startup gate is what refuses an unset mode, so a
    forgotten variable is a server that does not come up rather than one that serves
    HR content to anyone who can reach the port.
    """
    mode = auth_mode()
    if mode == "entra":
        return _entra_auth()
    if mode == "demo":
        return _demo_auth()
    return None


def refuse_unsafe_start(*, http: bool, host: str) -> None:
    """Exit rather than start in a configuration that looks secure and is not.

    Typing `off` IS the escape hatch. There is deliberately no second
    ALLOW_UNAUTHENTICATED flag: `off` shows up in the process listing, in the
    deployment manifest and on every `server_start` record, which is a better control
    than a flag nobody reads.
    """
    mode = auth_mode()
    if http and mode not in AUTH_MODES:
        raise SystemExit(
            f"KI_ICL_AUTH must be one of {', '.join(AUTH_MODES)} to serve over HTTP "
            f"(it is {mode!r}). Serving unauthenticated is allowed, but only by saying "
            f"so: KI_ICL_AUTH=off."
        )
    if mode == "entra" and os.environ.get("KI_ICL_DEV_PRINCIPAL", "").strip():
        raise SystemExit("KI_ICL_DEV_PRINCIPAL is refused in entra mode.")
    if mode == "entra" and dashboard_from_env():
        raise SystemExit(
            "KI_ICL_DASHBOARD is refused in entra mode. The dashboard has no "
            "authentication of its own, and its catalog panel lists every artifact id "
            "in the corpus regardless of grants, so it must not be reachable once "
            "there are real identities to gate. deploy/variables.tf refuses the same "
            "combination; this is the copy that survives an out-of-band update."
        )
    if mode == "demo" and http and host not in ("127.0.0.1", "::1", "localhost"):
        raise SystemExit(f"demo tokens are loopback-only; refusing to bind {host}.")
    # A policy that does not load becomes deny-all at runtime, which is the right
    # failure for a file that breaks *after* deployment but a terrible one to boot
    # into: every read refused, with the cause visible only as a log warning nobody is
    # watching yet. Refuse to start instead, so a bad deploy is loud.
    if not access.load_policy(artifacts.ARTIFACTS_ROOT, mode=access.Mode.ENFORCE).roles:
        raise SystemExit(
            f"No usable {access.POLICY_FILENAME} at {artifacts.ARTIFACTS_ROOT}. Every "
            f"read would be refused. Run `make package` in ki-ccl, or check CONTEXT_ROOT."
        )
    if mode == "entra" and identity.audit_key_from_env() is None:
        if os.environ.get("KI_ICL_AUDIT_REQUIRED", "").strip() == "1":
            raise SystemExit(
                "KI_ICL_AUDIT_REQUIRED=1 and no usable KI_ICL_AUDIT_KEY. Logging is a "
                "declared control here, so refusing to start rather than serve unlogged."
            )


LOOPBACK = ("127.0.0.1", "::1", "localhost")


def bind_address() -> tuple[str, int]:
    """Where to listen. Loopback and 8000 unless told otherwise.

    A container has to *ask* for a wider bind, by setting `KI_ICL_HOST=0.0.0.0`.
    Defaulting the other way would mean a careless local `--http` run exposes the
    corpus to whatever network the laptop is on, which is the kind of default that is
    discovered by accident rather than by design.
    """
    host = os.environ.get("KI_ICL_HOST", "").strip() or "127.0.0.1"
    raw_port = os.environ.get("KI_ICL_PORT", "").strip() or "8000"
    try:
        port = int(raw_port)
    except ValueError:
        raise SystemExit(f"KI_ICL_PORT must be a number, not {raw_port!r}.") from None
    return host, port


def startup_lines(
    *,
    host: str,
    port: int,
    http: bool,
    mode: access.Mode,
    auth: AuthProvider | None = None,
) -> list[str]:
    """What the operator sees on stderr at boot.

    A function rather than inline prints so the warnings can be tested. Each warning is
    absent when it does not apply: a banner that always fires stops being read, which is
    the same argument the review caveat rests on.
    """
    where = f"http://{host}:{port}/mcp" if http else "stdio"
    lines = [
        f"serving {artifacts.ARTIFACTS_ROOT} | {where} "
        f"| auth={auth_mode() or 'none (stdio)'} | access={mode.value}"
    ]
    if mode is not access.Mode.ENFORCE:
        lines.append(
            "WARNING: access grants are OBSERVED, not enforced. Every denial is logged "
            "with effect=observed and nothing is withheld."
        )
    if http and auth_mode() == "off" and host not in LOOPBACK:
        lines.append(
            f"WARNING: serving UNAUTHENTICATED on {host}:{port}. Every caller that can "
            f"reach this port reads the whole corpus. Only the surrounding network is "
            f"stopping them, and this process cannot tell whether there is one."
        )
    if http and dashboard_from_env() and host not in LOOPBACK:
        lines.append(
            f"WARNING: the usage dashboard is mounted at http://{host}:{port}/dashboard, "
            f"reading {usage_dashboard.LOG}. "
            f"It lists every artifact id in the corpus regardless of grants, and "
            f"/dashboard/purge rewrites the usage log. It has no authentication of its "
            f"own; only the surrounding network is stopping anyone who can reach this "
            f"port."
        )
    if identity.audit_key_from_env() is None:
        lines.append(
            "NOTE: no usable KI_ICL_AUDIT_KEY, so no actor is recorded. The log will "
            "say what was read, never by whom."
        )
    if isinstance(auth, entra_auth.EntraAuthProvider):
        config = auth.entra_config
        lines.append(
            f"entra tenant={config.tenant_id} client={config.client_id} "
            f"base_url={config.base_url}"
        )
    return lines


mcp = build_server(auth=auth_from_env(), dashboard=dashboard_from_env())


if __name__ == "__main__":
    http = "--http" in sys.argv
    host, port = bind_address()
    refuse_unsafe_start(http=http, host=host)

    mode = identity.effective_mode()
    for line in startup_lines(
        host=host, port=port, http=http, mode=mode, auth=mcp.auth
    ):
        print(line, file=sys.stderr)

    usage.USAGE_LOG.write(
        usage.server_start_record(
            auth_mode=auth_mode(), transport="http" if http else "stdio", mode=mode
        )
    )
    # One snapshot of what exists, so the dashboard can tell a miss for something that
    # never existed from a miss for something that was deleted.
    usage.USAGE_LOG.write(usage.catalog_record(artifacts.ARTIFACTS_ROOT))
    if http:
        mcp.run(
            transport="http",
            host=host,
            port=port,
            # FastMCP's "auto" engages only for a loopback bind, which is exactly right
            # in both places: it blocks DNS rebinding against a local server, and steps
            # aside behind an ingress whose hostname this process cannot know.
            host_origin_protection="auto",
        )
    else:
        mcp.run()

# The context server, with its corpus baked in.
#
# The corpus is built here rather than mounted, so the image tag answers "what was
# served when". A content edit means a rebuild and a new revision, which is the trade
# taken deliberately: immutability and an audit trail, against minutes of latency on a
# content change.
#
# Two stages because packaging needs git and the runtime does not. `version_id` on every
# artifact is the last commit that touched that artifact's folder, so the builder needs
# real history. Without it the packager falls back to GITHUB_SHA and then to
# "uncommitted", and every artifact would report the same opaque stamp.
#
#   az acr build --registry <acr> --image ki-icl:$(git rev-parse --short HEAD) .
#
# Python 3.12 because that is what CI validates against. The local venv is 3.14; nothing
# here depends on the difference, but the tested version is the one to ship.

FROM python:3.12-slim AS builder

# git for the version stamp, and nothing else. The packager is stdlib plus pyyaml.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /src
# Only what the gate and the packager need, which is pyyaml and the standard library.
# Installing requirements.txt here would pull FastMCP and its whole tree into a stage
# that never imports it: `server/access.py` is deliberately free of FastMCP, which is
# what lets `scripts/` import the sensitivity ladder from it.
RUN pip install --no-cache-dir "pyyaml>=6.0"

# .git comes with the context so `git log` can stamp each artifact. If you build from a
# tree without it, pass --build-arg GITHUB_SHA=<sha> and the packager uses that instead.
ARG GITHUB_SHA=""
ENV GITHUB_SHA=${GITHUB_SHA}

COPY . .

# Validate before packaging, so a corpus that fails the gate cannot become an image.
# This is the same gate CI runs, and it is cheaper to fail here than to deploy and
# discover a missing `sensitivity` label as a runtime denial.
RUN python scripts/validate_context.py \
 && python scripts/package_context.py


FROM python:3.12-slim AS runtime

# No git, no build tools, no test dependencies.
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY server/ ./server/
COPY --from=builder /src/dist/staging/ ./context/

# `config/demo_principals.yaml` is deliberately NOT copied. Demo identities cannot work
# here anyway (they are loopback-only and this binds 0.0.0.0), so shipping them would
# put a table of credential-shaped strings into a deployment for no reason. Their absence
# is not a failure: KI_ICL_DEV_PRINCIPAL degrades to anonymous with a warning, and entra
# mode refuses that variable outright.

# Non-root. The server only ever reads its corpus, so it needs nothing else.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin app \
 && chown -R app:app /app
USER app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CONTEXT_ROOT=/app/context \
    # Empty on purpose: stderr becomes the only sink, which is what production wants.
    # A file inside a container is a store nobody owns and nobody rotates, and Log
    # Analytics already collects stdout and stderr. This is also where the 90-day
    # retention is enforced: one store, one setting, one owner.
    CONTEXT_USAGE_LOG="" \
    # Container Apps routes to the container's own address, so loopback is not enough.
    # The server defaults to loopback and has to be asked for a wider bind; this is the
    # asking. The network boundary is the ingress, not this line.
    KI_ICL_HOST=0.0.0.0 \
    KI_ICL_PORT=8000

EXPOSE 8000

# KI_ICL_AUTH is deliberately NOT set here. The server refuses to serve HTTP without it,
# so a deployment that forgets to choose does not come up. That belongs in the
# deployment, where somebody is accountable for the choice, not baked into the image.
CMD ["python", "server/mcp_server.py", "--http"]

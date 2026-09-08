# The context server. The corpus is no longer part of this image.
#
# It used to be baked in here, on purpose: the image tag answered "what was served
# when," traded against needing a rebuild for every content edit. That trade is reversed
# now: the corpus is published separately to an Azure Files share and mounted read-only
# at CONTEXT_ROOT (see deploy/README.md, Stage 2), so a content edit no longer touches
# this image at all. The "what was served on Tuesday" property moves with it, carried by
# the publish step's own record-keeping (a share snapshot) rather than by this tag.
#
# One stage, because there is nothing left for a builder stage to do: validating and
# packaging the corpus (`scripts/validate_context.py`, `scripts/package_context.py`) now
# happens at publish time, not build time, and CI already runs both on every push
# independently of this image.
#
#   az acr build --registry <acr> --image ki-icl:$(git rev-parse --short HEAD) .
#
# Python 3.12 because that is what CI validates against.

FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY server/ ./server/

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
    # No longer baked content: a mount point. See deploy/README.md Stage 2. Runnable
    # locally too, pointed at dist/staging (`make serve-http`), in which case this is
    # just an ordinary directory.
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

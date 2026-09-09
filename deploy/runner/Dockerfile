# A self-hosted GitHub Actions runner, registered repo-scoped to ki-ccl only, that
# gives ki-ccl's publish.yml a path to the Files share's private endpoint without ever
# opening that account's public network access. See ../runner.tf for the Azure side
# and entrypoint.sh for what happens at container start.
#
#   az acr build --registry <acr_name> --image ki-ccl-runner:<sha> .
#   terraform apply -var runner_image=<acr_login_server>/ki-ccl-runner:<sha>
#
# Ubuntu, not slim/Alpine: the actions/runner release only ships glibc builds, and its
# bundled .NET runtime needs the ICU/libssl packages Ubuntu carries by default.

FROM ubuntu:22.04

ARG RUNNER_VERSION=2.337.0
# RUNNER_ARCH, deliberately not the obvious spelling: TARGETARCH is one of BuildKit's
# predefined platform args, so under any BuildKit-backed builder the declaration below
# would be overwritten with that builder's own value ("amd64") and this default would
# never apply. The release asset is published as "x64", so the build would then die on
# a 404 from a URL that reads as though it should work. A name BuildKit does not own
# cannot be clobbered, whichever builder ends up running this.
ARG RUNNER_ARCH=x64

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates jq git tar sudo \
  && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --shell /bin/bash runner \
  && usermod -aG sudo runner \
  && echo "runner ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/runner

RUN curl -fsSL -o /tmp/az-install.sh https://aka.ms/InstallAzureCLIDeb \
  && bash /tmp/az-install.sh \
  && rm /tmp/az-install.sh

USER runner
WORKDIR /home/runner

RUN curl -fsSL -o actions-runner.tar.gz \
    "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-${RUNNER_ARCH}-${RUNNER_VERSION}.tar.gz" \
  && tar xzf actions-runner.tar.gz \
  && rm actions-runner.tar.gz \
  && sudo ./bin/installdependencies.sh

COPY --chown=runner:runner entrypoint.sh /home/runner/entrypoint.sh
RUN chmod +x /home/runner/entrypoint.sh

ENTRYPOINT ["/home/runner/entrypoint.sh"]

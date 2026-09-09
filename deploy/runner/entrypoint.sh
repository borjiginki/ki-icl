#!/usr/bin/env bash
# Registers this runner against GITHUB_REPO and starts it. GITHUB_PAT is read from a
# Key Vault-backed Container App secret (see ../runner.tf); it needs only the
# Administration:write permission on that one repository, which is what lets this
# script mint a fresh, one-time-use registration token on every start rather than a
# long-lived token being baked in anywhere. `--replace` means a container restart
# re-registers cleanly under the same name instead of erroring on a stale registration.
#
# The registration token is separate from, and does not need refreshing alongside, the
# runner's own OAuth credential: config.sh trades the registration token for that
# credential once, here, and run.sh below refreshes it itself for as long as the
# process stays up.
set -euo pipefail

: "${GITHUB_REPO:?GITHUB_REPO is required, e.g. ki-group-gmbh/ki-ccl}"
: "${GITHUB_PAT:?GITHUB_PAT is required}"
RUNNER_LABELS="${RUNNER_LABELS:-ki-ccl-publish}"
RUNNER_NAME="${RUNNER_NAME:-ki-ccl-publish-$(hostname)}"

reg_token="$(
  curl -fsSL -X POST \
    -H "Authorization: token ${GITHUB_PAT}" \
    -H "Accept: application/vnd.github+json" \
    "https://api.github.com/repos/${GITHUB_REPO}/actions/runners/registration-token" \
    | jq -r .token
)"

cleanup() {
  removal_token="$(
    curl -fsSL -X POST \
      -H "Authorization: token ${GITHUB_PAT}" \
      -H "Accept: application/vnd.github+json" \
      "https://api.github.com/repos/${GITHUB_REPO}/actions/runners/remove-token" \
      | jq -r .token
  )"
  ./config.sh remove --token "${removal_token}" || true
}
trap cleanup EXIT TERM INT

./config.sh \
  --url "https://github.com/${GITHUB_REPO}" \
  --token "${reg_token}" \
  --name "${RUNNER_NAME}" \
  --labels "${RUNNER_LABELS}" \
  --unattended \
  --replace

./run.sh

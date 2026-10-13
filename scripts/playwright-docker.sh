#!/usr/bin/env bash
# Run the WebUI Playwright suite inside the official Playwright image, the same
# environment CI uses, so screenshot baselines are reproducible.
#
# Usage: scripts/playwright-docker.sh [playwright test args...]
#   scripts/playwright-docker.sh visual --update-snapshots
# Requires a built WebUI (pnpm --dir webui build). Set DOCKER="sudo docker" if needed.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
version="$(node -p "require('$root/webui/node_modules/@playwright/test/package.json').version")"

# shellcheck disable=SC2086 # DOCKER may contain a command prefix such as sudo.
${DOCKER:-docker} run --rm --init --ipc=host \
  --volume "$root:/work" --workdir /work \
  --volume verter-e2e-cache:/cache \
  --env CI="${CI:-}" --env HOST_UID="$(id -u)" --env HOST_GID="$(id -g)" \
  "mcr.microsoft.com/playwright:v${version}-noble" \
  bash -euo pipefail -c '
    export UV_CACHE_DIR=/cache/uv UV_PROJECT_ENVIRONMENT=/cache/venv UV_PYTHON_DOWNLOADS=never
    if [ ! -x /cache/bin/uv ]; then
      curl -LsSf https://astral.sh/uv/install.sh \
        | env UV_INSTALL_DIR=/cache/bin UV_NO_MODIFY_PATH=1 sh >/dev/null
    fi
    export PATH="/cache/bin:$PATH"
    uv sync --frozen --no-dev --python /usr/bin/python3.12 --quiet
    status=0
    (cd webui && ./node_modules/.bin/playwright test "$@") || status=$?
    chown -R "$HOST_UID:$HOST_GID" webui/e2e webui/test-results webui/playwright-report 2>/dev/null || true
    exit "$status"
  ' playwright "$@"

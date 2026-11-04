#!/usr/bin/env bash
# Launches Playwright's Chromium as a regular desktop browser (desktop environment).
set -euo pipefail
CHROME="$(ls -d "${PLAYWRIGHT_BROWSERS_PATH:-/ms-playwright}"/chromium-*/chrome-linux*/chrome 2>/dev/null | head -n1)"
if [ -z "${CHROME}" ]; then
  echo "Playwright Chromium not found" >&2
  exit 1
fi
exec "${CHROME}" --no-first-run --no-default-browser-check --disable-dev-shm-usage \
  --user-data-dir="${HOME}/.config/chromium-desktop" \
  --window-position=0,0 --window-size="${SCREEN_WIDTH:-1440},${SCREEN_HEIGHT:-900}" "$@"

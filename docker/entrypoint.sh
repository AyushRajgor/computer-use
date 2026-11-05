#!/usr/bin/env bash
# Starts the virtual display + live view (unless HEADLESS=true), then runs the
# agent CLI with the container arguments.
#
#   <flags...>       -> python main.py <flags...>
#   serve            -> keep the desktop running (use `docker compose exec`)
#   any other cmd    -> exec it (e.g. bash, pytest)
set -euo pipefail

SCREEN_WIDTH="${SCREEN_WIDTH:-1440}"
SCREEN_HEIGHT="${SCREEN_HEIGHT:-900}"
NOVNC_PORT="${NOVNC_PORT:-6080}"
VNC_PORT="${VNC_PORT:-5900}"
DISPLAY="${DISPLAY:-:99}"
export DISPLAY SCREEN_WIDTH SCREEN_HEIGHT
export SCREEN_SIZE="${SCREEN_SIZE:-${SCREEN_WIDTH}x${SCREEN_HEIGHT}}"

is_true() { case "${1:-}" in 1|true|TRUE|True|yes|on) return 0 ;; *) return 1 ;; esac; }

start_display() {
  local display_num="${DISPLAY#:}"
  rm -f "/tmp/.X${display_num}-lock" "/tmp/.X11-unix/X${display_num}" 2>/dev/null || true

  Xvfb "${DISPLAY}" -screen 0 "${SCREEN_WIDTH}x${SCREEN_HEIGHT}x24" -nolisten tcp -ac \
    >/tmp/xvfb.log 2>&1 &
  for _ in $(seq 1 50); do
    xdpyinfo -display "${DISPLAY}" >/dev/null 2>&1 && break
    sleep 0.1
  done
  if ! xdpyinfo -display "${DISPLAY}" >/dev/null 2>&1; then
    echo "Xvfb failed to start:" >&2; cat /tmp/xvfb.log >&2; exit 1
  fi

  fluxbox >/tmp/fluxbox.log 2>&1 &

  # Optional apps to open on the desktop, e.g. DESKTOP_STARTUP_APPS="xterm,chromium".
  local app
  IFS=',' read -r -a startup_apps <<< "${DESKTOP_STARTUP_APPS:-}"
  for app in "${startup_apps[@]}"; do
    app="$(echo "${app}" | xargs)"
    [ -n "${app}" ] || continue
    local log_name
    log_name="$(basename "${app%% *}")"
    (cd "${HOME}" && ${app} >"/tmp/startup-${log_name}.log" 2>&1 &)
  done

  local vnc_auth=(-nopw)
  if [ -n "${VNC_PASSWORD:-}" ]; then
    mkdir -p "${HOME}/.vnc"
    x11vnc -storepasswd "${VNC_PASSWORD}" "${HOME}/.vnc/passwd" >/dev/null 2>&1
    vnc_auth=(-rfbauth "${HOME}/.vnc/passwd")
  fi
  local view_only=()
  if is_true "${VNC_VIEW_ONLY:-false}"; then view_only=(-viewonly); fi
  x11vnc -display "${DISPLAY}" -forever -shared -quiet -rfbport "${VNC_PORT}" \
    "${vnc_auth[@]}" "${view_only[@]}" >/tmp/x11vnc.log 2>&1 &

  websockify --web /usr/share/novnc "${NOVNC_PORT}" "localhost:${VNC_PORT}" \
    >/tmp/novnc.log 2>&1 &

  export LIVE_VIEW_URL="${LIVE_VIEW_URL:-http://localhost:${NOVNC_PORT}/vnc.html?autoconnect=1&resize=scale}"
  echo "Live view (noVNC): ${LIVE_VIEW_URL}"
}

if is_true "${HEADLESS:-false}"; then
  export PLAYWRIGHT_HEADLESS=true
  unset DISPLAY
else
  start_display
fi

cd /app

if is_true "${RUN_LOGS:-true}" && ! { mkdir -p /app/runs 2>/dev/null && [ -w /app/runs ]; }; then
  echo "Note: /app/runs is not writable (mounted folder owned by another user); session logs are off." >&2
  export RUN_LOGS=false
fi

if [ "$#" -eq 0 ]; then set -- --help; fi

case "$1" in
  serve)
    echo "Desktop is running. Run tasks with:"
    echo "  docker compose exec desktop python main.py --env desktop --query \"...\""
    exec sleep infinity
    ;;
  -*)
    if ! is_true "${KEEP_DISPLAY_OPEN:-false}" || is_true "${HEADLESS:-false}"; then
      # exec: python receives SIGTERM directly (docker stop) and can release
      # cloud browser sessions before the container exits.
      exec python main.py "$@"
    fi
    # Keep the live view up afterwards: run python as a child, forward signals.
    python main.py "$@" <&0 &
    pid=$!
    trap 'kill -TERM "${pid}" 2>/dev/null; wait "${pid}"; exit 143' TERM INT
    status=0
    wait "${pid}" || status=$?
    trap 'exit 0' TERM INT
    echo "Agent exited with status ${status}. KEEP_DISPLAY_OPEN=true: the live view stays up (Ctrl+C to stop)."
    sleep infinity &
    wait $!
    exit "${status}"
    ;;
  *)
    exec "$@"
    ;;
esac

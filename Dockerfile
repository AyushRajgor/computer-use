# Gemini Computer Use -- sandboxed runtime.
#
# Contains: Python 3.12, Playwright Chromium, a virtual X display (Xvfb) with a
# window manager and a few desktop apps (for the `desktop` environment), and a
# noVNC live view on port 6080.
#
#   docker compose build
#   docker compose run --rm --service-ports agent --query "..."
#
# See README.md ("Docker") for all options.
ARG BASE_IMAGE=python:3.12-slim-bookworm
FROM ${BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    DEBIAN_FRONTEND=noninteractive

# Virtual display, VNC/noVNC, window manager, input tooling and desktop apps.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        xvfb x11vnc novnc websockify fluxbox xdotool x11-utils \
        xterm mousepad pcmanfm \
        fonts-liberation fonts-dejavu-core fonts-noto-color-emoji \
        ca-certificates curl tini procps \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt \
    && playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

COPY . .

# Desktop helpers: a `chromium` launcher for the desktop environment, a quiet
# wallpaper setter (avoids a startup popup) and the window-manager menu.
RUN install -m 0755 docker/chromium-launcher.sh /usr/local/bin/chromium \
    && install -m 0755 docker/fbsetbg /usr/local/bin/fbsetbg \
    && install -m 0755 docker/entrypoint.sh /usr/local/bin/entrypoint.sh \
    && useradd --create-home --uid 1000 --shell /bin/bash agent \
    && mkdir -p /home/agent/.fluxbox /home/agent/Desktop /app/runs \
    && cp docker/fluxbox-menu /home/agent/.fluxbox/menu \
    && printf 'session.screen0.toolbar.visible: true\nsession.screen0.workspaces: 1\n' > /home/agent/.fluxbox/init \
    && chown -R agent:agent /home/agent /app/runs

USER agent

ENV DISPLAY=:99 \
    SCREEN_WIDTH=1440 \
    SCREEN_HEIGHT=900 \
    NOVNC_PORT=6080 \
    PLAYWRIGHT_HEADLESS=false

EXPOSE 6080

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
CMD ["--help"]

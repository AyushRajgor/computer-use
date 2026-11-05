# Docker

The image is a ready-made sandbox: Python, Playwright Chromium, a virtual
display (Xvfb + Fluxbox), xdotool and a **noVNC live view** so you can watch
the agent in your own browser. It runs as a non-root user.

The *everyday Chrome* option (`--env cdp` with your own Chrome) needs Chrome on
the same computer, so it doesn't work from inside the container. Everything
else does.

## Run a task

```bash
cp .env.example .env      # add your keys
docker compose build
docker compose run --rm --service-ports agent --query "What's the weather in Paris?"
```

Open http://localhost:6080 to watch. Other browsers work the same way:

```bash
docker compose run --rm --service-ports agent --env browserbase --query "..."
docker compose run --rm --service-ports agent --env browser-use --query "..."
docker compose run --rm --service-ports agent --env desktop --query "Open the terminal and run uname -a"
```

Without a display or live view:

```bash
docker compose run --rm -e HEADLESS=true agent --query "..."
```

## A desktop that stays up

Start it once, then send tasks to it; watch at http://localhost:6081.

```bash
docker compose --profile desktop up -d desktop
docker compose exec desktop python main.py --env desktop --query "..."
```

## Good to know

* **Confirmations** are asked in your terminal (`-it` is on by default). For
  unattended runs pass `-e SAFETY_CONFIRMATION=deny` (or `auto-confirm`, only in
  a sandbox you trust).
* **Run logs** (trajectory + screenshots) go to `./runs`; set `RUN_LOG_DIR=` to
  turn them off.
* **Stopping**: Ctrl+C or `docker stop` release Browserbase sessions and stop
  Browser Use browsers before the container exits.
* **Ports** are bound to `127.0.0.1` only.
* **Plain Docker** without Compose:
  `docker build -t gemini-computer-use . && docker run --rm -it -p 127.0.0.1:6080:6080 --env-file .env gemini-computer-use --query "..."`
* **Docker Hub rate limits**: build from a mirror with
  `docker build --build-arg BASE_IMAGE=mirror.gcr.io/library/python:3.12-slim-bookworm .`

## Settings

| Variable | Default | Purpose |
|-|-|-|
| `HEADLESS` | `false` | `true` = no virtual display and no live view |
| `SCREEN_WIDTH` / `SCREEN_HEIGHT` | `1440` / `900` | Size of the virtual display |
| `LIVE_VIEW_PORT` | `6080` | Host port of the live view (`agent` service) |
| `DESKTOP_LIVE_VIEW_PORT` | `6081` | Host port of the live view (`desktop` service) |
| `VNC_PASSWORD` | none | Protect the live view with a password |
| `VNC_VIEW_ONLY` | `false` | Watch only, no mouse/keyboard from the live view |
| `KEEP_DISPLAY_OPEN` | `false` | Keep the live view up after the agent finishes |
| `DESKTOP_STARTUP_APPS` | `xterm` for `desktop` | Apps to open at start, e.g. `xterm,chromium` |
| `RUN_LOG_DIR` | `/app/runs` | Where run logs are written (empty = off) |

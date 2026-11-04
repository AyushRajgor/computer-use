# Gemini Computer Use

A ready-to-run agent for the **Gemini Computer Use** tool (GA, October 2026).
Gemini looks at screenshots and decides every click, keystroke and scroll; this
repository executes those actions in a browser (local or cloud) or on a Linux
desktop, feeds back new screenshots, and loops until the task is done.

| Where the agent acts | `--env` | Runs on |
|-|-|-|
| Local Chromium / Chrome / Edge (Playwright) | `playwright` (default) | your machine or Docker |
| Any browser exposing CDP (your own Chrome, a self-hosted browser container, ...) | `cdp` | wherever that browser runs |
| [Browserbase](https://www.browserbase.com) cloud browser | `browserbase` | Browserbase cloud |
| [Browser Use](https://browser-use.com) cloud browser | `browser-use` | Browser Use cloud |
| Linux desktop (X11, `environment="desktop"`) | `desktop` | Docker (recommended) or any X11 Linux |

What's new compared to the original `computer-use-preview` code:

* **Interactions API** (`client.interactions.create`) is the default; the legacy
  `generateContent` API is still available with `--api generate_content`.
* **New models**: `gemini-3.8-flash` (default, recommended), `gemini-4-argon`,
  `gemini-3.6-flash`, `gemini-3.5-flash-lite`. The shut-down
  `gemini-2.5-computer-use-preview-10-2025` model was removed from the defaults.
* **Current action set** with `intent` (plus the legacy action set for older models),
  **desktop environment**, **configurable safety policies**, **prompt injection
  detection**, **custom functions** (incl. a `yield_to_user` human-in-the-loop
  tool), **thinking levels** and **media resolution**.
* **Cloud browsers**: fully configurable Browserbase and Browser Use Cloud backends
  (proxies, CAPTCHA solving, persistent contexts/profiles, recordings, live view...).
* **Docker** image with a virtual display and a **noVNC live view** at
  http://localhost:6080.
* Domain **allowlist / blocklist**, run **logs with screenshots**, sane exit codes,
  every flag configurable through environment variables.
* Optional alternative engine: the open-source **browser-use** agent driven by Gemini.

> [!CAUTION]
> Computer Use may contain errors and security vulnerabilities. Supervise it
> closely, run it in a sandbox (the Docker image), and avoid tasks involving
> critical decisions or sensitive data. See the
> [safety best practices](https://aistudio.google.com/docs/computer-use#safety-best-practices).

---

## Quick start (local)

Requires **Python 3.11+**.

```bash
git clone https://github.com/AyushRajgor/computer-use.git
cd computer-use

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Browser for the local Playwright environment
playwright install --with-deps chromium

cp .env.example .env      # then set GEMINI_API_KEY
python main.py --query "Go to Google and search for 'Gemini Computer Use'"
```

Get an API key at [Google AI Studio](https://aistudio.google.com/apikey).

### Using the Gemini Enterprise Agent Platform (Vertex AI) instead

```bash
export USE_VERTEXAI=true
export VERTEXAI_PROJECT="your-project-id"
export VERTEXAI_LOCATION="global"   # the Interactions API on the platform is global-only (preview)
gcloud auth application-default login
```

If your project/region does not have the Interactions API yet, use
`--api generate_content` (and any supported location).

---

## Environments

### Local browser (`--env playwright`)

```bash
python main.py --query "Find the cheapest flight from SFO to HNL on Jun 30"
python main.py --query "..." --initial_url "https://www.google.com/travel/flights"
python main.py --query "..." --headless                 # no window
python main.py --query "..." --browser_channel chrome   # installed Google Chrome
python main.py --query "..." --user_data_dir ~/.cu-profile  # keep cookies / logins
```

### Your own browser over CDP (`--env cdp`)

Attach to any Chromium-based browser that exposes the DevTools protocol, e.g.
your regular Chrome (start it with a separate profile):

```bash
google-chrome --remote-debugging-port=9222 --user-data-dir=/tmp/cu-chrome
python main.py --env cdp --cdp_url http://localhost:9222 --query "..."
```

This also works with self-hosted browser containers or any provider that
returns a CDP websocket URL. The browser is left running when the agent exits.

### Browserbase (`--env browserbase`)

```bash
export BROWSERBASE_API_KEY=...
python main.py --env browserbase --query "..."
```

The session URL (`https://browserbase.com/sessions/<id>`) and a **live view**
URL are printed at start; the session is released on exit. Options (all
optional, set in `.env`):

| Variable | Purpose |
|-|-|
| `BROWSERBASE_PROJECT_ID` | Project (inferred from the API key if unset) |
| `BROWSERBASE_REGION` | `us-west-2`, `us-east-1`, `eu-central-1`, `ap-southeast-1` |
| `BROWSERBASE_PROXIES` / `BROWSERBASE_PROXY_COUNTRY` / `_STATE` / `_CITY` | Managed residential proxies, optionally geo-located |
| `BROWSERBASE_SOLVE_CAPTCHAS`, `BROWSERBASE_BLOCK_ADS` | CAPTCHA solving (default on), ad blocking |
| `BROWSERBASE_VERIFIED`, `BROWSERBASE_OS` | Verified browser (Scale plan; replaces "advanced stealth"), OS fingerprint |
| `BROWSERBASE_CONTEXT_ID`, `BROWSERBASE_PERSIST_CONTEXT` | Persistent cookies / logins across sessions |
| `BROWSERBASE_RECORD_SESSION`, `BROWSERBASE_LOG_SESSION` | Session replay and logs |
| `BROWSERBASE_EXTENSION_ID` | Load an uploaded Chrome extension |
| `BROWSERBASE_KEEP_ALIVE`, `BROWSERBASE_SESSION_TIMEOUT` | Session lifetime |
| `BROWSERBASE_DOWNLOADS_DIR` | Save files the agent downloaded (zip) on exit |

Helpers for one-time setup:

```python
from computers.browserbase.browserbase import create_context, upload_extension
print(create_context())                 # -> BROWSERBASE_CONTEXT_ID
print(upload_extension("my-ext.zip"))   # -> BROWSERBASE_EXTENSION_ID
```

> Browserbase does not offer a self-hostable browser. To run "locally" use
> `--env playwright` (or `--env cdp` with your own browser) -- same agent, same actions.

### Browser Use Cloud (`--env browser-use`)

```bash
export BROWSER_USE_API_KEY=...
python main.py --env browser-use --query "..."
```

Creates a standalone cloud browser (stealth Chromium, residential proxy, CAPTCHA
handling), prints its **live view** URL, and **stops** it on exit (Browser Use
bills until a browser is stopped or times out). Options:

| Variable | Purpose |
|-|-|
| `BROWSER_USE_PROXY_COUNTRY` | Proxy country (`us` by default on the server); `none` disables the proxy |
| `BROWSER_USE_CUSTOM_PROXY` | Your own proxy: `[user:pass@]host:port` |
| `BROWSER_USE_PROFILE_ID` | Persistent profile (cookies, logins) -- one per end user is recommended |
| `BROWSER_USE_SESSION_TIMEOUT` | Minutes (1-240, default 60) |
| `BROWSER_USE_SOLVE_CAPTCHAS` | Default on |
| `BROWSER_USE_ENABLE_RECORDING` | Record the session; the recording URL is printed after the run |
| `BROWSER_USE_EXTENSION_IDS`, `BROWSER_USE_ALLOW_RESIZING` | Extensions (max 3), window resizing |

The open-source `browser-use` library is an agent framework rather than a
browser; it is available as an optional engine (see below).

### Linux desktop (`--env desktop`)

Gemini controls a whole X11 desktop with OS-level mouse and keyboard
(`environment="desktop"`, no `navigate`/`go_back` actions). Use the Docker image:

```bash
docker compose run --rm --service-ports agent --env desktop \
  --query "Open the terminal and show the kernel version"
```

Watch it at http://localhost:6080. The desktop has a terminal, a browser
(`chromium`), a text editor and a file manager (right-click the desktop for the
menu). On a Linux host you can also point it at an existing X display
(`DISPLAY=:1 python main.py --env desktop ...`) -- **never on your real session**.

---

## Docker

The image bundles Python, Playwright Chromium, a virtual display (Xvfb +
Fluxbox), xdotool and a noVNC live view, and runs as a non-root user.

```bash
cp .env.example .env              # add your keys
docker compose build

# One task, watch live at http://localhost:6080
docker compose run --rm --service-ports agent --query "What's the weather in Paris?"
docker compose run --rm --service-ports agent --env browserbase --query "..."
docker compose run --rm --service-ports agent --env browser-use --query "..."

# Headless (no display / live view)
docker compose run --rm -e HEADLESS=true agent --query "..."

# A long-running desktop you can watch and send tasks to (http://localhost:6081)
docker compose --profile desktop up -d desktop
docker compose exec desktop python main.py --env desktop --query "..."
```

* Run logs (trajectory + screenshots) are written to `./runs` (set `RUN_LOG_DIR=` to disable).
* Safety confirmations are asked in the terminal (`-it` is on by default). For
  unattended runs use `-e SAFETY_CONFIRMATION=deny` or `auto-confirm`.
* `VNC_PASSWORD` protects the live view; `VNC_VIEW_ONLY=true` makes it read-only;
  `KEEP_DISPLAY_OPEN=true` keeps it up after the agent finishes. Ports are bound
  to `127.0.0.1` only.
* Include the optional browser-use engine: `WITH_BROWSER_USE=true docker compose build`.
* Different base image / registry mirror (e.g. Docker Hub rate limits):
  `docker build --build-arg BASE_IMAGE=mirror.gcr.io/library/python:3.12-slim-bookworm .`
* Plain Docker: `docker build -t gemini-computer-use . && docker run --rm -it -p 127.0.0.1:6080:6080 --env-file .env gemini-computer-use --query "..."`

---

## Models

| Model | Status | Notes |
|-|-|-|
| `gemini-3.8-flash` | **Default, recommended** | High-accuracy UI interaction; thinking `low`/`medium` (default)/`high` (`minimal` not supported) |
| `gemini-4-argon` | GA | Most capable; complex reasoning and agentic UI workflows |
| `gemini-3.6-flash` | GA | Previous Flash generation |
| `gemini-3.5-flash-lite` | GA | Low latency, cost-effective |
| `gemini-3-flash-preview` | Preview | Legacy action names; no prompt injection detection |
| `gemini-3.7-flash` | Deprecated | Requests are routed to `gemini-3.8-flash` |
| `gemini-3.5-flash` | Deprecated | Requests are routed to `gemini-3.6-flash` |
| `gemini-2.5-computer-use-preview-10-2025` | **Shut down** (July 28, 2026) | Use `gemini-3.8-flash` |

```bash
python main.py --model gemini-4-argon --query "..."
python main.py --thinking_level low --query "..."   # faster; usually enough for UI automation
```

Source: [model versions](https://aistudio.google.com/docs/computer-use#model-versions),
[deprecations](https://aistudio.google.com/docs/deprecations).

---

## Computer Use features

### APIs and conversation history

| Flag | Behavior |
|-|-|
| `--api interactions` (default) | Interactions API. Each turn is chained with `previous_interaction_id`; the server keeps the history (stored 55 days paid / 1 day free tier). |
| `--api interactions --history client` | Stateless: `store=false`, the full history (incl. thought signatures) is sent every turn and screenshots older than `--max_screenshots` turns are dropped -- cheaper for long tasks, nothing stored server-side. |
| `--api generate_content` | Legacy `generateContent`, client-side history with screenshot trimming. |

### Safety

* **Confirmations** -- when the model returns `safety_decision: require_confirmation`
  the agent asks you (`--safety ask`, default). Without a terminal it denies.
  `--safety deny` always stops; `--safety auto-confirm` approves automatically
  (only in a sandbox). Blocked actions always stop the run.
* **Safety policies** -- `--disable_safety_policy data_modification` (repeatable)
  expresses a preference to relax a category: `financial_transactions`,
  `sensitive_data_modification`, `communication_tool`, `account_creation`,
  `data_modification`, `user_consent_management`, `legal_terms_and_agreements`.
  The model may still ask for confirmation.
* **Prompt injection detection** -- screenshots are scanned for hidden adversarial
  instructions. On by default for models that support it (3.5 Flash-Lite and
  later); `--no-prompt_injection_detection` turns it off.
* **Domain allow/block lists** -- `--allowed_domains google.com,wikipedia.org`
  / `--blocked_domains example.com`. Enforced for every top-level navigation
  (clicks, redirects, `navigate`), and passed to Browserbase as well.
* **System instruction** -- `--system_instruction_file my_rules.md` to add your own
  rules (see the example in the
  [docs](https://aistudio.google.com/docs/computer-use#safety-best-practices)).

### Human in the loop

`--hitl` adds a `yield_to_user` function: the model can pause and ask you for
something (a 2FA code, a CAPTCHA, a choice). Type the answer, or press Enter after
doing it yourself in the browser/live view.

### Custom functions and excluded actions

```bash
python main.py --exclude_action drag_and_drop --exclude_action hotkey --query "..."
```

Add your own tools in Python -- see `custom_functions.py`:

```python
from custom_functions import CustomFunction

LOOKUP_ORDER = CustomFunction(
    name="lookup_order",
    description="Looks up the shipping status of an order by id.",
    parameters={"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]},
    handler=lambda args: {"status": "shipped", "order_id": args["order_id"]},
)
```

### Observability

`--log_dir runs` writes `runs/<timestamp>/trajectory.jsonl` (model text,
intents, function calls, safety decisions, results, token usage), one PNG per
action and `result.json`. Token usage is printed at the end of every run.

---

## Optional engine: browser-use (driven by Gemini)

The default engine is Gemini Computer Use (screenshots + coordinates). As an
alternative you can run a task with the open-source
[browser-use](https://docs.browser-use.com) agent, which mainly works from the
page's DOM (numbered elements) and uses screenshots as extra context. Gemini
still makes every decision.

```bash
./scripts/install_engines.sh      # browser-use pins an old google-genai; this applies overrides
python main.py --engine browser-use --query "..."                       # local Chrome
python main.py --engine browser-use --env browser-use --query "..."     # Browser Use Cloud browser
python main.py --engine browser-use --env browserbase --query "..."     # Browserbase browser
python main.py --engine browser-use --env cdp --cdp_url http://localhost:9222 --query "..."
```

> **Stagehand** (Browserbase's open-source framework) is not included as an
> engine: Stagehand 4.x removed its `agent()` API (only `act` / `observe` /
> `extract` remain), and the 3.x line with an agent is superseded.

---

## CLI reference

`python main.py --help` lists everything. Each flag has an environment variable
default (see `.env.example`).

| Flag | Env var | Default |
|-|-|-|
| `--query` | | (required) |
| `--env` | `COMPUTER_USE_ENV` | `playwright` |
| `--engine` | `COMPUTER_USE_ENGINE` | `gemini` |
| `--model` | `GEMINI_MODEL` | `gemini-3.8-flash` |
| `--initial_url` | `INITIAL_URL` | `https://www.google.com` |
| `--api` | `GEMINI_API` | `interactions` |
| `--history` | `GEMINI_HISTORY` | `server` |
| `--thinking_level` | `GEMINI_THINKING_LEVEL` | model default |
| `--media_resolution` | `GEMINI_MEDIA_RESOLUTION` | model default |
| `--[no-]prompt_injection_detection` | `GEMINI_PROMPT_INJECTION_DETECTION` | on if supported |
| `--disable_safety_policy` | `GEMINI_DISABLED_SAFETY_POLICIES` | none |
| `--exclude_action` | `GEMINI_EXCLUDED_ACTIONS` | none |
| `--system_instruction[_file]` | `GEMINI_SYSTEM_INSTRUCTION[_FILE]` | none |
| `--safety` | `SAFETY_CONFIRMATION` | `ask` |
| `--hitl` | `HUMAN_IN_THE_LOOP` | off |
| `--max_steps` | `MAX_STEPS` | `100` (0 = unlimited) |
| `--max_screenshots` | `MAX_RECENT_SCREENSHOTS` | `3` |
| `--log_dir` | `RUN_LOG_DIR` | off (`/app/runs` in Docker) |
| `--screen_size` | `SCREEN_SIZE` | `1440x900` |
| `--[no-]headless` | `PLAYWRIGHT_HEADLESS` | headless only without a display |
| `--browser_channel` | `PLAYWRIGHT_CHANNEL` | `chromium` |
| `--user_data_dir` | `BROWSER_USER_DATA_DIR` | temporary profile |
| `--proxy_server` | `BROWSER_PROXY_SERVER` | none |
| `--cdp_url` | `CDP_URL` | |
| `--allowed_domains` / `--blocked_domains` | `ALLOWED_DOMAINS` / `BLOCKED_DOMAINS` | none |
| `--tab_mode` | `TAB_MODE` | `single` (`follow` switches to new tabs/popups) |
| `--highlight_mouse` | | off |
| `--quiet` | | off |

The exit code is `0` when the task completed, `1` otherwise.

## Python API

```python
from agent import ComputerUseAgent
from computers import BrowserbaseComputer, PlaywrightComputer
from custom_functions import YIELD_TO_USER

with PlaywrightComputer(screen_size=(1440, 900), initial_url="https://www.google.com") as computer:
    agent = ComputerUseAgent(
        computer,
        query="Find the opening hours of the Louvre",
        model_name="gemini-3.8-flash",
        thinking_level="low",
        custom_functions=[YIELD_TO_USER],
        safety_mode="ask",
    )
    result = agent.run()          # AgentResult(status, final_text, steps, usage, log_dir, error)
    print(result.status, result.final_text)
```

`BrowserAgent` and `agent_loop()` still exist for backwards compatibility.

## Project layout

```
main.py                 CLI
agent.py                ComputerUseAgent: loop, action dispatch, safety, logging
backends.py             Interactions API + generateContent backends
models.py               model registry, action names, safety policies
custom_functions.py     custom function support + yield_to_user
computers/
  computer.py           Computer interface (implement it to add an environment)
  playwright/           local browser (base class for all browser backends)
  cdp/                  any CDP browser
  browserbase/          Browserbase cloud browser
  browser_use/          Browser Use Cloud browser
  desktop/              X11 desktop (xdotool + mss)
engines/                optional: browser-use engine
docker/                 entrypoint, desktop launcher, window manager menu
Dockerfile, docker-compose.yml
tests/                  unit tests + integration tests (real Chromium / Xvfb, skipped if missing)
```

## Testing

```bash
pip install -r requirements.txt
playwright install --with-deps chromium   # for the browser integration test
pytest
```

The tests use a local fake Gemini API server, so no API keys are needed; the
real `google-genai` SDK still builds and parses every request.

## Migrating from `computer-use-preview`

* Default model `gemini-3.6-flash` -> `gemini-3.8-flash`; `gemini-2.5-computer-use-preview-10-2025` is shut down.
* `generate_content` -> Interactions API by default (`--api generate_content` keeps the old path).
* Removed sampling parameters (`temperature`, `top_p`, `top_k`) -- not supported by Gemini 3.x models.
* `drag_and_drop` now uses `start_x/start_y/end_x/end_y` and `scroll` uses
  `magnitude_in_pixels` (legacy argument names are still accepted).
* `PLAYWRIGHT_HEADLESS=false` now really means headful (the old check treated any value as true).
* `BROWSERBASE_PROJECT_ID` is optional; the Browserbase session is now explicitly released.
* Python 3.11+ is required (google-genai 2.x).
* Tests moved to `tests/`.

## Known Issues

### Playwright Dropdown Menu

On certain operating systems, the Playwright browser is unable to capture
`<select>` elements because they are rendered by the operating system. As a
result, the agent is unable to send the correct screenshot to the model.

There are several ways to mitigate this.

1. Use a cloud browser (`--env browserbase` / `--env browser-use`) or the Docker image.
2. Inject a script like [proxy-select](https://github.com/amitamb/proxy-select) to
   render a custom `<select>` element. You must inject `proxy-select.css` and
   `proxy-select.js` into each page that has a non-custom `<select>` element.
   You can do this in `PlaywrightComputer._configure` by adding a few lines of
   code, like the following (replacing `PROXY_SELECT_JS` and `PROXY_SELECT_CSS`
   with the appropriate variables):

```python
self._context.add_init_script(PROXY_SELECT_JS)
def inject_style(page):
    try:
        page.add_style_tag(content=PROXY_SELECT_CSS)
    except Exception as e:
        print(f"Error injecting style: {e}")

self._page.on('domcontentloaded', inject_style)
```

Note, option 2 does not work 100% of the time, but is a temporary workaround for
certain websites.

## References

* [Computer Use docs](https://aistudio.google.com/docs/computer-use) ·
  [Interactions API](https://aistudio.google.com/docs/interactions-overview) ·
  [Migrating to Interactions](https://aistudio.google.com/docs/migrate-to-interactions)
* [Browserbase docs](https://docs.browserbase.com) ·
  [Browser Use Cloud docs](https://docs.browser-use.com/cloud/quickstart)

# Gemini Computer Use

Let Gemini use a web browser for you. Gemini looks at the screen and decides
every click, keystroke and scroll; this project carries out those actions and
repeats until the task is done. You choose where the browser runs: a fresh
local Chromium, **your everyday Chrome**, or a cloud browser from
**Browserbase** or **Browser Use**.

## 1. Requirements

* Python 3.11 or newer (or Docker, see [below](#run-with-docker))
* A Gemini API key -- free at [Google AI Studio](https://aistudio.google.com/apikey)

## 2. Install

```bash
git clone https://github.com/AyushRajgor/computer-use.git
cd computer-use
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium        # only needed for option A below
```

## 3. Add your keys

```bash
cp .env.example .env               # Windows: copy .env.example .env
```

Open `.env` and fill in the keys for the browser you want to use:

| Browser | `--env` | Keys in `.env` |
|-|-|-|
| A. Fresh local Chromium | `playwright` (default) | `GEMINI_API_KEY` |
| B. Your everyday Chrome | `cdp` | `GEMINI_API_KEY` |
| C. Browserbase (cloud) | `browserbase` | `GEMINI_API_KEY`, `BROWSERBASE_API_KEY` |
| D. Browser Use (cloud) | `browser-use` | `GEMINI_API_KEY`, `BROWSER_USE_API_KEY` |

Where to get the cloud keys:
[Browserbase](https://www.browserbase.com/sessions) (shown at the top of the dashboard) ·
[Browser Use](https://cloud.browser-use.com/settings?tab=api-keys) (Settings → API keys).

## 4. Run

### A. Fresh local Chromium

```bash
python main.py --query "Search Google for the weather in Paris"
```

A Chromium window opens and you can watch it work. Add `--headless` to hide it.

### B. Your everyday Chrome

Uses the Chrome you already have open, with your bookmarks and signed-in
accounts (Chrome 144+).

1. In Chrome, open `chrome://inspect/#remote-debugging` and check
   **Allow remote debugging for this browser instance**.
2. Run:
   ```bash
   python main.py --env cdp --query "Search Google for the weather in Paris"
   ```
3. Click **Allow** when Chrome asks.

The agent works in its own new tab and leaves your other tabs alone.
**It acts as you on every site you're signed in to** -- keep an eye on it and
consider `--allowed_domains`. Using Edge: add `--cdp_url msedge`.
[More details and troubleshooting](docs/browsers.md#your-everyday-chrome---env-cdp).

### C. Browserbase

```bash
python main.py --env browserbase --query "Search Google for the weather in Paris"
```

A live-view link is printed so you can watch; the session is closed at the end.

### D. Browser Use

```bash
python main.py --env browser-use --query "Search Google for the weather in Paris"
```

A live-view link is printed; the cloud browser is stopped at the end.

### Long or multi-line tasks

Leave out `--query` and paste the task when asked. Newlines, numbered lists
and quotes are kept exactly as written. When done, press Enter, then
**Ctrl+D** (macOS / Linux) or **Ctrl+Z and Enter** (Windows):

```bash
python main.py --env cdp
```

Or keep the task in a text file:

```bash
python main.py --env cdp --query_file task.txt
```

## Run with Docker

A sandbox with the browser included, plus a live view at http://localhost:6080.
Works for options A, C and D (B needs Chrome on your own computer).

```bash
cp .env.example .env               # add your keys
docker compose build
docker compose run --rm --service-ports agent --query "Search Google for the weather in Paris"
```

Add `--env browserbase` or `--env browser-use` for the cloud browsers. More in
[docs/docker.md](docs/docker.md).

## Useful options

| Option | What it does |
|-|-|
| `--model gemini-4-argon` | Use the most capable model (default: `gemini-3.8-flash`) |
| `--initial_url https://...` | Start on a specific page |
| `--headless` | Hide the local browser window |
| `--max_steps 50` | Stop after 50 steps (default 100) |
| `--allowed_domains example.com` | Only let the agent visit these sites |
| `--hitl` | Let the agent ask you for input, e.g. a 2FA code |
| `--no-log` | Don't save this session |
| `--no-open_report` | Don't open the report in your browser when the run ends |

Every run is saved as a session in `runs/` (steps, tool calls, timing, tokens,
screenshots). When a run ends, completed or not, the session's report opens
in your browser: a page with the answer or error, every step and its
screenshot. List sessions with `python runs.py`. More in
[docs/features.md](docs/features.md#session-logs).

To skip typing `--env` every time, set `COMPUTER_USE_ENV` in `.env`. All
options: `python main.py --help` or [docs/configuration.md](docs/configuration.md).

> [!CAUTION]
> Computer Use can make mistakes. Watch it on important tasks and avoid
> sensitive ones. Before anything consequential (purchases, sending messages,
> accepting terms) the agent asks you to confirm in the terminal. See
> [Google's safety guidance](https://aistudio.google.com/docs/computer-use#safety-best-practices).

## Learn more

* [Browsers](docs/browsers.md) -- every option for the four browsers above, a
  dedicated agent profile, other CDP browsers, the Linux desktop environment
* [Configuration](docs/configuration.md) -- all settings, models, Google Cloud
  (Vertex AI) setup, exit codes
* [Docker](docs/docker.md) -- live view, long-running desktop, settings
* [Features](docs/features.md) -- safety controls, human in the loop, custom
  functions, run logs, Python API
* [Development](docs/development.md) -- design, project layout, tests, changes
  from Google's preview repo

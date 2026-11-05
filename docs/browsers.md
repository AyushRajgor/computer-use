# Browsers and environments

Pick where the agent acts with `--env` (or `COMPUTER_USE_ENV` in `.env`).
Gemini makes every decision in all of them; they only differ in where the
browser runs.

| `--env` | Browser | Needs (besides `GEMINI_API_KEY`) |
|-|-|-|
| `playwright` (default) | A fresh Chromium on your machine | `playwright install chromium` |
| `cdp` | Your everyday Chrome, or any browser exposing CDP | Chrome 144+ with remote debugging allowed |
| `browserbase` | Browserbase cloud browser | `BROWSERBASE_API_KEY` |
| `browser-use` | Browser Use cloud browser | `BROWSER_USE_API_KEY` |
| `desktop` | A whole Linux desktop | The Docker image (or an X11 display) |

Options that work for every browser:

| Flag | Env var | Purpose |
|-|-|-|
| `--initial_url` | `INITIAL_URL` | Page to open first (default `https://www.google.com`) |
| `--screen_size` | `SCREEN_SIZE` | Viewport, default `1440x900` (recommended by Google) |
| `--allowed_domains` | `ALLOWED_DOMAINS` | Comma-separated allowlist; any other site is blocked |
| `--blocked_domains` | `BLOCKED_DOMAINS` | Comma-separated blocklist |
| `--tab_mode` | `TAB_MODE` | `single` (default): links that open a new tab load in the current one. `follow`: switch to new tabs/popups (e.g. "Sign in with Google" windows) |
| `--highlight_mouse` | | Draw a red circle where the agent clicks |

The domain lists are checked on every top-level navigation (clicks, redirects,
typed URLs). With a list active, `file:` and `chrome:` URLs are blocked too.

---

## Local Chromium (`--env playwright`)

```bash
python main.py --query "..."
```

A Chromium window opens; it is closed when the agent finishes.

| Flag | Env var | Purpose |
|-|-|-|
| `--headless` / `--no-headless` | `PLAYWRIGHT_HEADLESS` | Hide the window. Default: visible if there is a display, headless otherwise (SSH, WSL, CI) |
| `--browser_channel` | `PLAYWRIGHT_CHANNEL` | `chrome` or `msedge` to launch your installed Chrome/Edge app instead of Playwright's Chromium |
| `--user_data_dir` | `BROWSER_USER_DATA_DIR` | A profile folder that survives between runs, so logins are kept. Use a new folder, not your everyday Chrome profile (Chrome refuses to automate that one this way; see below) |
| `--proxy_server` | `BROWSER_PROXY_SERVER` | e.g. `http://proxy.local:3128` |

Example -- a dedicated, persistent "agent" profile in your installed Chrome:

```bash
python main.py --browser_channel chrome --user_data_dir ~/.gemini-agent-profile --query "..."
```

Log in to the sites the agent needs once in that window; the logins are kept
for the next runs.

---

## Your everyday Chrome (`--env cdp`)

The agent attaches to the Chrome you already have open, with your normal
profile, bookmarks and signed-in accounts.

**Requirements:** Chrome 144 or newer, running on the same computer as the agent.

1. In Chrome, open `chrome://inspect/#remote-debugging` and check
   **Allow remote debugging for this browser instance**. Keep Chrome open.
2. Run:
   ```bash
   python main.py --env cdp --query "..."
   ```
3. Chrome asks whether to allow the connection: click **Allow**.

How it behaves:

* The agent opens **its own tab**, keeps it in front while it works and leaves
  it open afterwards so you can see the result. Your other tabs are not
  touched, and tabs you open meanwhile are ignored (use another window to keep
  browsing).
* Disconnecting does not close Chrome.
* `--allowed_domains` / `--blocked_domains` apply to the whole browser while
  the agent is connected.
* Other browsers: `--cdp_url msedge` (also `chrome-beta`, `chrome-dev`,
  `chrome-canary`, `msedge-beta`, `msedge-dev`, `msedge-canary`).

> [!WARNING]
> The agent acts as **you** on every site your profile is signed in to (email,
> banking, shopping...). A malicious page can try to trick it. Keep
> `--safety ask` (the default), consider `--allowed_domains`, and watch it work.
> If you'd rather not share your main profile, use the dedicated agent profile
> shown in the previous section.

Why this way: since Chrome 136, the classic `--remote-debugging-port` switch is
ignored for the default profile (to stop cookie-stealing malware). The
`chrome://inspect` switch is Chrome's supported replacement; Playwright finds
the running browser through the `DevToolsActivePort` file Chrome then writes in
its profile folder.

**Troubleshooting**

* *"DevToolsActivePort file not found"* -- Chrome is not running, or the
  checkbox in `chrome://inspect/#remote-debugging` is not checked.
* *Connection times out* -- look for the **Allow** dialog in Chrome (it may be
  behind other windows).
* Doesn't work inside Docker or on a remote server: Chrome and the agent must
  run on the same computer.

### Any other browser over CDP

Give `--cdp_url` a URL instead of a browser name to attach to any browser that
exposes the DevTools protocol: a Chrome started with its own profile, a
self-hosted browser container (browserless, steel-browser, ...), or a provider
that hands out a CDP websocket URL.

```bash
google-chrome --remote-debugging-port=9222 --user-data-dir=/tmp/cu-chrome
python main.py --env cdp --cdp_url http://localhost:9222 --query "..."
```

The agent works in the browser's first tab; the browser keeps running when the
agent exits.

---

## Browserbase (`--env browserbase`)

Needs `BROWSERBASE_API_KEY` (shown at the top of the
[Browserbase dashboard](https://www.browserbase.com/sessions)).

```bash
python main.py --env browserbase --query "..."
```

The session page (`https://browserbase.com/sessions/<id>`) and a **live view**
link are printed at start. The session is released when the agent finishes, is
stopped with Ctrl+C, or the container is stopped.

Optional settings (`.env`):

| Variable | Purpose |
|-|-|
| `BROWSERBASE_PROJECT_ID` | Project; inferred from the API key if unset |
| `BROWSERBASE_REGION` | `us-west-2`, `us-east-1`, `eu-central-1`, `ap-southeast-1` |
| `BROWSERBASE_PROXIES` | `true` = managed residential proxies |
| `BROWSERBASE_PROXY_COUNTRY` / `_STATE` / `_CITY` | Geo-located proxy, e.g. `US` / `CA` / `San Francisco` |
| `BROWSERBASE_SOLVE_CAPTCHAS` | CAPTCHA solving (on by default at Browserbase) |
| `BROWSERBASE_BLOCK_ADS` | Block ads |
| `BROWSERBASE_VERIFIED` | Verified browser (Scale plan; replaces "advanced stealth") |
| `BROWSERBASE_OS` | `windows`, `mac`, `linux`, `mobile`, `tablet` |
| `BROWSERBASE_CONTEXT_ID` + `BROWSERBASE_PERSIST_CONTEXT=true` | Keep cookies / logins across sessions |
| `BROWSERBASE_RECORD_SESSION`, `BROWSERBASE_LOG_SESSION` | Session replay and logs |
| `BROWSERBASE_EXTENSION_ID` | Load an uploaded Chrome extension |
| `BROWSERBASE_KEEP_ALIVE`, `BROWSERBASE_SESSION_TIMEOUT` | Session lifetime (timeout in seconds, 60-21600) |
| `BROWSERBASE_IGNORE_CERTIFICATE_ERRORS` | Accept invalid TLS certificates |
| `BROWSERBASE_DOWNLOADS_DIR` | Save the files the agent downloaded (zip) on exit |

One-time helpers for contexts and extensions:

```python
from computers.browserbase.browserbase import create_context, upload_extension
print(create_context())                 # -> BROWSERBASE_CONTEXT_ID
print(upload_extension("my-ext.zip"))   # -> BROWSERBASE_EXTENSION_ID
```

Browserbase has no self-hostable browser; to run locally use `--env playwright`
or `--env cdp` -- same agent, same actions.

---

## Browser Use (`--env browser-use`)

Needs `BROWSER_USE_API_KEY`
([Browser Use Cloud → Settings → API keys](https://cloud.browser-use.com/settings?tab=api-keys)).

```bash
python main.py --env browser-use --query "..."
```

Starts a stealth cloud Chromium (residential proxy and CAPTCHA handling
included), prints its **live view** link and **stops** it at the end -- Browser
Use bills until a browser is stopped or times out.

Optional settings (`.env`):

| Variable | Purpose |
|-|-|
| `BROWSER_USE_PROXY_COUNTRY` | Proxy country (`us` by default); `none` disables the proxy |
| `BROWSER_USE_CUSTOM_PROXY` | Your own proxy: `[user:pass@]host:port` |
| `BROWSER_USE_PROFILE_ID` | Persistent profile (cookies, logins); one per end user is recommended |
| `BROWSER_USE_SESSION_TIMEOUT` | Minutes (1-240, default 60) |
| `BROWSER_USE_SOLVE_CAPTCHAS` | On by default |
| `BROWSER_USE_ENABLE_RECORDING` | Record the session; the recording link is printed after the run |
| `BROWSER_USE_EXTENSION_IDS` | Up to 3 extension ids, comma-separated |
| `BROWSER_USE_ALLOW_RESIZING` | Allow window resizing (reduces stealth) |

Only Browser Use's *cloud browser* is used; its open-source agent library is not
part of this project (see [development.md](development.md#design)).

---

## Linux desktop (`--env desktop`)

Gemini controls a whole desktop with OS-level mouse and keyboard
(`environment="desktop"`; there are no `navigate` / `go_back` actions). Use the
Docker image:

```bash
docker compose run --rm --service-ports agent --env desktop \
  --query "Open the terminal and show the kernel version"
```

Watch it at http://localhost:6080. The desktop has a terminal, a browser, a
text editor and a file manager (right-click the desktop for the menu). See
[docker.md](docker.md) for the long-running desktop service.

On a Linux host you can point it at an existing X display
(`DISPLAY=:1 python main.py --env desktop ...`; needs `xdotool`) -- **never on
your real desktop session**.

---

## Known issue: dropdowns in the local browser

On some operating systems Playwright cannot capture native `<select>` dropdowns
(they are drawn by the OS), so the model doesn't see the open list. Use a cloud
browser or the Docker image, or inject a script such as
[proxy-select](https://github.com/amitamb/proxy-select) in
`PlaywrightComputer._configure` (`self._context.add_init_script(...)` plus a
`domcontentloaded` handler that adds its CSS). The workaround doesn't help on
every site.

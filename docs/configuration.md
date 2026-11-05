# Configuration

Settings come from command-line flags; most flags also read a default from an
environment variable, so you can put them in `.env`. Flags always win.

`.env.example` only lists what a new user needs (the API keys and the default
browser). Everything else is documented here and in [browsers.md](browsers.md).

## Gemini credentials

**Gemini Developer API (most people):** set `GEMINI_API_KEY` (`GOOGLE_API_KEY`
also works). Get a key at [Google AI Studio](https://aistudio.google.com/apikey).

**Gemini Enterprise Agent Platform (formerly Vertex AI):**

| Variable | Value |
|-|-|
| `USE_VERTEXAI` | `true` (or `GOOGLE_GENAI_USE_ENTERPRISE=true`) |
| `VERTEXAI_PROJECT` | Your Google Cloud project (or `GOOGLE_CLOUD_PROJECT`) |
| `VERTEXAI_LOCATION` | `global` (or `GOOGLE_CLOUD_LOCATION`); the Interactions API there is global-only while in preview |

Then authenticate with `gcloud auth application-default login`. If your project
doesn't have the Interactions API yet, add `--api generate_content` (any
supported location works there).

## Models

| Model | Status | Notes |
|-|-|-|
| `gemini-3.8-flash` | **Default, recommended** | Thinking `low` / `medium` (default) / `high`; `minimal` is not supported |
| `gemini-4-argon` | GA | Most capable; complex reasoning and long UI workflows |
| `gemini-3.6-flash` | GA | Previous Flash generation |
| `gemini-3.5-flash-lite` | GA | Lowest latency and cost |
| `gemini-3-flash-preview` | Preview | Legacy action names; no prompt injection detection |
| `gemini-3.7-flash` | Deprecated | Requests are routed to `gemini-3.8-flash` |
| `gemini-3.5-flash` | Deprecated | Requests are routed to `gemini-3.6-flash` |
| `gemini-2.5-computer-use-preview-10-2025` | **Shut down** (July 28, 2026) | Use `gemini-3.8-flash` |

`--thinking_level low` is faster and usually enough for UI automation.
Sources: [model versions](https://aistudio.google.com/docs/computer-use#model-versions),
[deprecations](https://aistudio.google.com/docs/deprecations).

## All flags

`python main.py --help` prints the same list.

**Task and model**

| Flag | Env var | Default | Purpose |
|-|-|-|-|
| `--query` | | (required) | The task |
| `--env` | `COMPUTER_USE_ENV` | `playwright` | `playwright`, `cdp`, `browserbase`, `browser-use`, `desktop` ([browsers.md](browsers.md)) |
| `--model` | `GEMINI_MODEL` | `gemini-3.8-flash` | See the table above |
| `--initial_url` | `INITIAL_URL` | `https://www.google.com` | First page |
| `--thinking_level` | `GEMINI_THINKING_LEVEL` | model default | `low`, `medium`, `high` (`minimal` on some models) |
| `--media_resolution` | `GEMINI_MEDIA_RESOLUTION` | model default | Screenshot resolution sent to the model: `low` ... `ultra_high` |
| `--max_steps` | `MAX_STEPS` | `100` | Stop after this many model turns (`0` = unlimited) |
| `--api` | `GEMINI_API` | `interactions` | `generate_content` for the legacy API ([features.md](features.md#apis-and-conversation-history)) |
| `--history` | `GEMINI_HISTORY` | `server` | `client` = stateless, nothing stored server-side |
| `--max_screenshots` | `MAX_RECENT_SCREENSHOTS` | `3` | Screenshots kept in client-side history |

**Safety** ([features.md](features.md#safety))

| Flag | Env var | Default | Purpose |
|-|-|-|-|
| `--safety` | `SAFETY_CONFIRMATION` | `ask` | Answer to "this action needs confirmation": `ask`, `deny`, `auto-confirm` |
| `--hitl` | `HUMAN_IN_THE_LOOP` | off | Let the model ask you for input (2FA codes, ...) |
| `--[no-]prompt_injection_detection` | `GEMINI_PROMPT_INJECTION_DETECTION` | on if supported | Scan screenshots for hidden instructions |
| `--disable_safety_policy` | `GEMINI_DISABLED_SAFETY_POLICIES` | none | Relax a safety category (repeatable / comma-separated) |
| `--exclude_action` | `GEMINI_EXCLUDED_ACTIONS` | none | Remove a predefined action, e.g. `drag_and_drop` |
| `--system_instruction` / `--system_instruction_file` | `GEMINI_SYSTEM_INSTRUCTION` / `_FILE` | none | Your own rules for the model |
| `--allowed_domains` / `--blocked_domains` | `ALLOWED_DOMAINS` / `BLOCKED_DOMAINS` | none | Comma-separated site lists |

**Browser** ([browsers.md](browsers.md))

| Flag | Env var | Default | Purpose |
|-|-|-|-|
| `--screen_size` | `SCREEN_SIZE` | `1440x900` | Viewport |
| `--[no-]headless` | `PLAYWRIGHT_HEADLESS` | visible if a display exists | Local browser only |
| `--browser_channel` | `PLAYWRIGHT_CHANNEL` | `chromium` | `chrome`, `msedge`: launch the installed app |
| `--user_data_dir` | `BROWSER_USER_DATA_DIR` | temporary | Persistent profile folder for the local browser |
| `--proxy_server` | `BROWSER_PROXY_SERVER` | none | Proxy for the local browser |
| `--cdp_url` | `CDP_URL` | `chrome` | `--env cdp`: a browser name or a CDP URL |
| `--tab_mode` | `TAB_MODE` | `single` | `follow` switches to new tabs / popups |
| `--highlight_mouse` | | off | Show clicks in the page |

**Output**

| Flag | Env var | Default | Purpose |
|-|-|-|-|
| `--log_dir` | `RUN_LOG_DIR` | off | Save a trajectory and screenshots per run ([features.md](features.md#run-logs)) |
| `--quiet` | | off | Print only the final answer |

Cloud browser settings (`BROWSERBASE_*`, `BROWSER_USE_*`) are listed in
[browsers.md](browsers.md); Docker settings in [docker.md](docker.md).

## Exit codes

| Code | Meaning |
|-|-|
| `0` | The task completed |
| `1` | The agent stopped early: error, step limit, denied confirmation, or interrupted (Ctrl+C / `docker stop`) |
| `2` | Setup problem: missing API key, no display, bad option |
| `130` | Interrupted while still starting up |

Cloud browser sessions are released / stopped in every case.

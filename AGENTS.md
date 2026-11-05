## What this is

A CLI and Python library that lets Gemini Computer Use drive a browser (or a Linux desktop). Gemini is the **only decision-maker**: it reads screenshots and returns every action with coordinates; this code executes them and loops. It is a fork of Google's `computer-use-preview` (Apache-2.0; non-empty `.py` files carry the Google license header). Agent frameworks with their own decision loop (the `browser-use` library, Stagehand) are deliberately excluded. Only Browser Use's and Browserbase's *cloud browsers* are used.

Python 3.11+ is required (google-genai 2.x). There is no linter/formatter config and no CI.

## Commands

On Windows use the venv interpreter directly (`.\.venv\Scripts\python.exe`) or activate with `.venv\Scripts\Activate.ps1`.

```powershell
pip install -r requirements.txt
playwright install chromium                     # local browser + browser integration tests

python -m pytest                                # all tests (no API keys needed)
python -m pytest -m "not integration"           # skip tests that drive a real browser / X display
python -m pytest tests/test_agent.py::TestLoop::test_max_steps   # one test

python main.py --query "..."                    # run (default --env playwright)
python main.py --env cdp --query_file task.txt  # attach to the user's Chrome; task from a file
python main.py --help                           # every flag
python runs.py                                  # list saved sessions; `python runs.py 1 --html` rebuilds a report
```

Docker (needed for `--env desktop`): `docker compose build`, then `docker compose run --rm --service-ports agent --query "..."` (live view at http://localhost:6080).

## Architecture

The request path is `main.py` → `agent.ComputerUseAgent` → `backends.ModelBackend` (talks to Gemini) and `computers.Computer` (executes actions).

- **`main.py`**: argparse CLI. Nearly every flag takes its default from an env var via `_env`/`_env_bool`/`_env_int` (flags win; `.env` is loaded). `build_computer()` maps `--env` to a `Computer` class. `run_gemini()` creates the client and resolves the query *before* starting a browser so setup errors fail fast. Exit codes: 0 completed, 1 agent stopped early, 2 setup error (`ValueError`/`RuntimeError`/... raised during setup), 130 interrupted during startup. SIGTERM is turned into `KeyboardInterrupt` so cloud sessions are always released.
- **`agent.py`**: `ComputerUseAgent.run()` is the loop: send query + screenshot, execute each returned call, send one `ToolResult` per call, repeat until a turn has no calls. `handle_action()` is the single dispatch table from Gemini action names to `Computer` methods and accepts **both** current names (`click`, `type`, `scroll`, `hotkey`, `drag_and_drop` with `start_x/...`) and legacy ones (`click_at`, `type_text_at`, `scroll_document`, ...). Model coordinates are on a 0–1000 grid; `denormalize_x/y` scale to pixels and clamp, so `Computer` methods always receive pixels. Exceptions from an action are **reported back to the model** as an error `function_result`, not raised; stopping the run goes through `_TerminateLoop(status, message)`. Statuses: `completed`, `max_steps`, `terminated`, `error`. `safety_decision` in call args triggers `_get_safety_confirmation` (`ask`/`deny`/`auto-confirm`) and adds `safety_acknowledgement` to the result.
- **`backends.py`**: normalizes both Gemini APIs to `ToolCall` / `ToolResult` / `ModelTurn`. `InteractionsBackend` (default) with `history="server"` (chained by `previous_interaction_id`) or `"client"` (`store=false`, full history resent, old screenshots trimmed). `GenerateContentBackend` is the legacy API. Network retries (`call_with_retries`, retryable HTTP codes) live here; empty/malformed-turn retries (`retryable_empty`) are driven by the agent's `_next_turn`.
- **`models.py`**: model registry (`ModelInfo`: status, allowed thinking levels, prompt-injection-detection support, replacement) and the predefined action-name lists per environment, plus the safety policy names. Lookup strips any `models/...` or resource prefix. `PRICES` holds each model's dated paid-tier Standard rates; `estimate_cost` prices one request's `usage` (the report adds up the steps, since long-prompt tiers apply per request).
- **`computers/`**: `Computer` ABC (`computer.py`); every action returns an `EnvState` (screenshot PNG bytes, url, metadata). A class attribute `environment` (`"browser"`/`"desktop"`) flows into the backend config and decides which predefined actions Gemini gets. Inheritance matters: `PlaywrightComputer` is the base for **all** browsers (actions, `DomainPolicy` enforced by request routing, tab handling); `CDPComputer` overrides `_connect` to attach over CDP; `BrowserbaseComputer` and `BrowserUseComputer` subclass `CDPComputer` and only add session create/stop (`_cdp_endpoint`, `start_session`, `stop_session`, `_disconnect`, `live_view_url`). `DesktopComputer` is standalone (xdotool + mss on X11). Computers are context managers; cleanup in `__exit__` must release billed cloud sessions.
- **`custom_functions.py`**: `CustomFunction` (name, JSON-schema params, handler, `returns_screenshot`) converted for either API. `YIELD_TO_USER` is what `--hitl` enables.
- **Session logs**: `telemetry.SessionLog` writes `runs/<session id>/` (`run.json` at start, `trajectory.jsonl` per step, `step_*.png`, `result.json` at end). Logging must **never stop a run**: write failures turn the log off with a warning. `telemetry.load_session`/`Session` is the read side, shared by `runs.py` (terminal) and `report.py` (self-contained `report.html`). The report escapes all model/web content and uses a CSP: treat that content as untrusted when changing it. `main.run_settings` strips credentials from URLs before they are recorded.

Adding a predefined action touches `models.py` (name list), `agent.handle_action` (dispatch + argument normalization), and `Computer` + its implementations.

## Tests

- No network or keys: `tests/conftest.py` runs `FakeGeminiServer` on localhost and points a real `genai.Client` at it, so the SDK still builds and parses every request. Use the `fake_gemini` fixture and the `interaction(calls=..., text=...)` helper to script Interactions API responses; `server.requests` records what was sent. `fake_gemini.pages` also serves static HTML for browser tests.
- `tests/test_agent.py` mostly uses a `MagicMock` computer and a `ScriptedBackend` that returns canned `ModelTurn`s.
- An autouse fixture deletes `GEMINI_*`, `BROWSERBASE_*`, `BROWSER_USE_*`, `GOOGLE_GENAI_*` and a list of other env vars so the developer's `.env` can't leak in. Add any new env var that changes behavior to that list.
- `@pytest.mark.integration` tests drive a real Chromium (`CHROMIUM_EXECUTABLE` overrides the binary) or an Xvfb desktop; they skip when unavailable (the desktop ones always skip on Windows).

## Docs to keep in sync

User-facing docs are detailed and list every flag and env var: `README.md`, `docs/configuration.md` (all flags, models table, exit codes), `docs/browsers.md` (per-environment and cloud-provider env vars), `docs/features.md` (safety, session logs, Python API), `docs/docker.md`, and `.env.example`. When adding or renaming a flag, env var, model, or session-log field, update the matching doc.

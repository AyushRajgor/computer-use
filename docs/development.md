# Development

## Design

Gemini Computer Use is the only decision-maker: it reads the screenshots and
returns every action with coordinates. The browser providers (local Chromium,
your Chrome, Browserbase, Browser Use) are just places for those actions to
run. Agent frameworks that bring their own decision loop -- the open-source
`browser-use` library and Browserbase's Stagehand -- are therefore deliberately
not included.

The loop:

1. Send the task and a screenshot with the `computer_use` tool enabled.
2. Receive function calls (UI actions with an `intent`, maybe a `safety_decision`).
3. Execute them on a `Computer` (browser or desktop).
4. Send back one `function_result` per call (JSON plus a new screenshot).
5. Repeat until the model answers without function calls.

## Project layout

```
main.py                 CLI
agent.py                ComputerUseAgent: loop, action dispatch, safety, telemetry
telemetry.py            session ids and the runs/<session id>/ files (write + read)
runs.py                 CLI to list and inspect sessions in the terminal
report.py               a session as a page: report.html
backends.py             Interactions API + generateContent backends
models.py               model registry, action names, safety policies
custom_functions.py     custom function support + yield_to_user
computers/
  computer.py           Computer interface (implement it to add an environment)
  playwright/           local browser (base class for all browser backends)
  cdp/                  your Chrome / any CDP browser
  browserbase/          Browserbase cloud browser
  browser_use/          Browser Use cloud browser
  desktop/              X11 desktop (xdotool + mss)
docker/                 entrypoint, desktop launcher, window manager menu
docs/                   documentation
tests/                  unit + integration tests
```

## Tests

```bash
pip install -r requirements.txt
playwright install --with-deps chromium   # for the browser integration tests
pytest
```

No API keys are needed: the tests talk to a local fake Gemini API server, while
the real `google-genai` SDK still builds and parses every request. Integration
tests drive a real Chromium (and an Xvfb desktop on Linux with `xdotool` and
`xterm`); they are skipped when those aren't installed. Set
`CHROMIUM_EXECUTABLE` to test against a specific browser binary.

## Changes from Google's `computer-use-preview`

* Default model `gemini-3.6-flash` -> `gemini-3.8-flash`;
  `gemini-2.5-computer-use-preview-10-2025` is shut down.
* The Interactions API is the default; `--api generate_content` keeps the old path.
* Sampling parameters (`temperature`, `top_p`, `top_k`) removed -- Gemini 3.x
  models don't support them.
* `drag_and_drop` uses `start_x/start_y/end_x/end_y` and `scroll` uses
  `magnitude_in_pixels` (legacy argument names are still accepted).
* New: desktop environment, safety policies, prompt injection detection,
  thinking level, custom functions, your-Chrome and Browser Use backends, Docker.
* `PLAYWRIGHT_HEADLESS=false` now really means a visible browser (the old check
  treated any value as true).
* `BROWSERBASE_PROJECT_ID` is optional and the Browserbase session is released
  explicitly.
* Python 3.11+ is required (google-genai 2.x); tests live in `tests/`.

## References

* [Computer Use](https://aistudio.google.com/docs/computer-use) ·
  [Interactions API](https://aistudio.google.com/docs/interactions-overview) ·
  [Migrating to Interactions](https://aistudio.google.com/docs/migrate-to-interactions)
* [Browserbase docs](https://docs.browserbase.com) ·
  [Browser Use Cloud docs](https://docs.browser-use.com/cloud/quickstart)

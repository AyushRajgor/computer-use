# Features

## Safety

* **Confirmations.** When Gemini flags an action as needing confirmation
  (`safety_decision: require_confirmation`, e.g. a purchase or sending a
  message), `--safety` decides:
  * `ask` (default): you are asked in the terminal. Without a terminal it denies.
  * `deny`: the run stops.
  * `auto-confirm`: approved automatically. Only use this in a sandbox.

  Actions the safety service blocks always stop the run.
* **Safety policies.** `--disable_safety_policy data_modification` (repeatable)
  asks the model to be less strict about a category: `financial_transactions`,
  `sensitive_data_modification`, `communication_tool`, `account_creation`,
  `data_modification`, `user_consent_management`, `legal_terms_and_agreements`.
  It's a preference; the model may still ask for confirmation.
* **Prompt injection detection.** Screenshots are checked for hidden
  instructions ("ignore previous commands..."). On by default for models that
  support it (3.5 Flash-Lite and later); `--no-prompt_injection_detection` turns
  it off.
* **Domain allow/block lists.** `--allowed_domains google.com,wikipedia.org` or
  `--blocked_domains example.com`, enforced for every top-level navigation
  (clicks, redirects, typed URLs) and passed on to Browserbase.
* **Your own rules.** `--system_instruction_file rules.md`; Google's
  [safety best practices](https://aistudio.google.com/docs/computer-use#safety-best-practices)
  include a ready-made example.

## Human in the loop

`--hitl` gives the model a `yield_to_user` function: it can pause and ask you
for something (a 2FA code, a CAPTCHA, a choice). Type the answer, or press
Enter after doing the step yourself in the browser or live view.

## Custom functions and excluded actions

Remove built-in actions:

```bash
python main.py --exclude_action drag_and_drop --exclude_action hotkey --query "..."
```

Add your own tools in Python (see `custom_functions.py`):

```python
from custom_functions import CustomFunction

LOOKUP_ORDER = CustomFunction(
    name="lookup_order",
    description="Looks up the shipping status of an order by id.",
    parameters={"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]},
    handler=lambda args: {"status": "shipped", "order_id": args["order_id"]},
)
```

Pass it with `ComputerUseAgent(..., custom_functions=[LOOKUP_ORDER])`.

## Session logs

Every run is a session, saved to `runs/<session id>/` in the project folder.
This is on by default; `--no-log` (or `RUN_LOGS=false`) turns it off.

A session id is the start time plus a random suffix, e.g.
`20261105-141500-a3f9c1`: it sorts by time, tells you when the run happened,
and stays unique when two runs start in the same second.

| File | What's in it |
|-|-|
| `run.json` | Written at the start: the task, model, API, environment, every CLI setting, versions |
| `trajectory.jsonl` | One line per step: start time, time waiting for Gemini (`model_s`), time running actions (`actions_s`), the model's text and thoughts, each tool call with its arguments, result, start and end time (`started_at`, `ended_at`), `duration_s` and error flag, tokens, and any retries |
| `step_*.png` | The screenshot after each action |
| `result.json` | Written at the end: status, answer or error, duration, time split, tokens, tool call counts, failures, retries |
| `report.html` | Written at the end: the session as a page (see below) |

The step that ends a run early (an error, `--max_steps`, a denied safety
prompt, Ctrl+C) is logged too, marked with `"stopped"`. A session without a
`result.json` was killed or is still running.

The end of every run prints a summary with a link to the report. It is printed
whether the run completed, failed, hit `--max_steps` or was stopped with Ctrl+C,
and the report opens in your browser (`--no-open_report` leaves it closed). The
link works with Ctrl+click in most terminals; VS Code and Cursor open it in the
editor instead.

```
Agent finished: completed after 7 steps in 1m 32s

  The Louvre is open 9:00-18:00 every day except Tuesday.

  Time     58.2s waiting for Gemini, 21.4s running actions
  Tools    12 calls: click 5, type 3, navigate 2, scroll 2 (1 failed)
  Tokens   48,210 total (input 46,900, output 410, thought 900)

  Report   file:///D:/projects/computer-use/runs/20261105-141500-a3f9c1/report.html
  Session  20261105-141500-a3f9c1  (in the terminal: python runs.py 20261105-141500-a3f9c1)
```

The report shows how the run ended (the answer, or the error and the step it
stopped at), its estimated cost (input, cached input and output at the paid
tier's Standard rates for the model on the day of the run; each step's cost
too), the totals (time split between Gemini and actions, steps, actions,
tokens), and the task. Overview, Activity and Settings tabs separate the result,
execution log and run configuration. The overview shows the session result
with one screenshot preview, regardless of the number of captures. Controls
above it let you move backward/forward or jump to any capture using a selector;
the preview supports left/right arrow keys, Home and End. A View step link opens
the corresponding action in Activity. Summary figures show their breakdowns
directly. Long answers and tasks have a Show full control; copying and printing
include the full text. In Activity, the timeline lets you inspect each step:
the model's reasoning, each action in words with its arguments and result,
and the screenshot after it. Click the preview or a step's screenshot to page
through captures full size with ← and →. The report starts in light mode;
the theme button switches to dark mode and remembers your choice when browser
storage is available. Tabs support arrow keys, Home and End; links to steps
open Activity directly. Without JavaScript, all sections remain visible.
It works offline and links screenshots by name, so the session folder can be
zipped and shared.

In Docker, the summary prints `runs/<session id>/report.html` instead of a
link, since a `file://` link would point inside the container: open it from
the project folder.

See sessions in the terminal with `runs.py`:

```bash
python runs.py                  # every session: status, steps, time, tokens, tools, task
python runs.py 1                # one session step by step (1 = newest in the list)
python runs.py latest --html    # rebuild that session's report.html and open it
```

A session can also be picked by its id, or enough of its start or end
(`python runs.py a3f9c1`). `--html` makes a report for a session that has
none, such as one whose process was killed.

Logs contain screenshots of whatever the agent saw. `runs/` is ignored by git.

## APIs and conversation history

| Flags | Behavior |
|-|-|
| `--api interactions` (default) | The Interactions API. Turns are chained with `previous_interaction_id`; Google keeps the history (55 days on the paid tier, 1 day on the free tier). |
| `--api interactions --history client` | Stateless: `store=false`, the whole history is sent each turn and screenshots older than `--max_screenshots` turns are dropped. Nothing is stored server-side; cheaper on long tasks. |
| `--api generate_content` | The legacy `generateContent` API, with client-side history and screenshot trimming. |

## Python API

```python
from agent import ComputerUseAgent
from computers import PlaywrightComputer
from custom_functions import YIELD_TO_USER

with PlaywrightComputer(screen_size=(1440, 900), initial_url="https://www.google.com") as computer:
    agent = ComputerUseAgent(
        computer,
        query="Find the opening hours of the Louvre",
        model_name="gemini-3.8-flash",
        thinking_level="low",
        custom_functions=[YIELD_TO_USER],
        safety_mode="ask",
        log_dir="runs",    # save the session (the CLI does this by default)
    )
    result = agent.run()
    print(result.status, result.final_text)
    print(result.session_id, result.duration_s, result.tool_calls, result.usage)
    print(result.report_path)  # runs/<session id>/report.html
```

`AgentResult` also has `model_s`, `actions_s`, `tool_errors`, `retries`,
`started_at`, `ended_at`, `error` and `log_dir`. Without `log_dir` nothing is
written, but the numbers are still returned.

The other browsers are `CDPComputer`, `BrowserbaseComputer`,
`BrowserUseComputer` and `DesktopComputer` (all in `computers`). `BrowserAgent`
and `agent_loop()` still exist for backwards compatibility.

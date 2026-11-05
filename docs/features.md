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

## Run logs

`--log_dir runs` writes `runs/<timestamp>/`:

* `trajectory.jsonl`: per step the model's text and intents, the function
  calls, safety decisions, results and token usage
* one PNG screenshot per action
* `result.json`: final status and answer

Token usage is also printed at the end of every run.

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
    )
    result = agent.run()   # AgentResult(status, final_text, steps, usage, log_dir, error)
    print(result.status, result.final_text)
```

The other browsers are `CDPComputer`, `BrowserbaseComputer`,
`BrowserUseComputer` and `DesktopComputer` (all in `computers`). `BrowserAgent`
and `agent_loop()` still exist for backwards compatibility.

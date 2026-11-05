# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""The Gemini Computer Use agent loop.

    1. Send the task (+ a screenshot) with the `computer_use` tool enabled.
    2. Receive function calls (UI actions with an `intent`, maybe a
       `safety_decision`).
    3. Execute them on a `Computer` (browser or desktop).
    4. Send back a `function_result` per call (JSON + new screenshot).
    5. Repeat until the model answers without function calls.

See https://aistudio.google.com/docs/computer-use
"""
import dataclasses
import datetime
import json
import os
import pathlib
import sys
import time
from typing import Any, Callable, Literal, Optional, Sequence, Union

import termcolor
from google import genai
from rich.console import Console
from rich.table import Table

from backends import (
    BackendConfig,
    GenerateContentBackend,
    InteractionsBackend,
    ModelBackend,
    ModelTurn,
    ToolCall,
    ToolResult,
)
from computers import Computer, EnvState
from computers.computer import UnsupportedActionError
from custom_functions import CustomFunction
from models import (
    ALL_PREDEFINED_FUNCTIONS,
    DEFAULT_MODEL,
    SAFETY_POLICIES,
    THINKING_LEVELS,
    get_model_info,
)

console = Console()

ApiName = Literal["interactions", "generate_content"]
SafetyMode = Literal["ask", "auto-confirm", "deny"]
AgentStatus = Literal["completed", "max_steps", "terminated", "error"]

# Built-in Computer Use actions return "EnvState"; custom functions return "dict".
FunctionResponseT = Union[EnvState, dict]

MAX_WAIT_SECONDS = 60
MAX_EMPTY_RETRIES = 3

# Kept for backwards compatibility with earlier versions of this repository.
MAX_RECENT_TURN_WITH_SCREENSHOTS = 3


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def create_client() -> genai.Client:
    """Creates a GenAI client for the Gemini Developer API or the Gemini
    Enterprise Agent Platform (formerly Vertex AI).

    Developer API: set GEMINI_API_KEY (or GOOGLE_API_KEY).
    Agent Platform: set USE_VERTEXAI=true (or GOOGLE_GENAI_USE_ENTERPRISE=true)
    plus VERTEXAI_PROJECT / VERTEXAI_LOCATION (or GOOGLE_CLOUD_PROJECT /
    GOOGLE_CLOUD_LOCATION). The Interactions API on Agent Platform currently
    only supports the `global` location.
    """
    use_enterprise = (
        _env_flag("USE_VERTEXAI")
        or _env_flag("GOOGLE_GENAI_USE_ENTERPRISE")
        or _env_flag("GOOGLE_GENAI_USE_VERTEXAI")
    )
    if use_enterprise:
        return genai.Client(
            enterprise=True,
            project=os.environ.get("VERTEXAI_PROJECT")
            or os.environ.get("GOOGLE_CLOUD_PROJECT"),
            location=os.environ.get("VERTEXAI_LOCATION")
            or os.environ.get("GOOGLE_CLOUD_LOCATION")
            or "global",
        )
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError(
            "GEMINI_API_KEY is not set. Get a key at https://aistudio.google.com/apikey "
            "and add it to your .env file (see .env.example)."
        )
    return genai.Client(api_key=api_key)


@dataclasses.dataclass
class AgentResult:
    status: AgentStatus
    final_text: Optional[str]
    steps: int
    usage: dict[str, int]
    log_dir: Optional[str] = None
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.status == "completed"


class _TerminateLoop(Exception):
    def __init__(self, status: AgentStatus, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class ComputerUseAgent:
    """Runs a Gemini Computer Use agent against a `Computer`."""

    def __init__(
        self,
        browser_computer: Computer,
        query: str,
        model_name: str = DEFAULT_MODEL,
        verbose: bool = True,
        *,
        api: ApiName = "interactions",
        history: Literal["server", "client"] = "server",
        environment: Optional[str] = None,
        thinking_level: Optional[str] = None,
        media_resolution: Optional[str] = None,
        enable_prompt_injection_detection: Optional[bool] = None,
        disabled_safety_policies: Sequence[str] = (),
        excluded_predefined_functions: Sequence[str] = (),
        custom_functions: Sequence[CustomFunction] = (),
        system_instruction: Optional[str] = None,
        safety_mode: SafetyMode = "ask",
        max_steps: int = 100,
        max_recent_screenshots: int = MAX_RECENT_TURN_WITH_SCREENSHOTS,
        send_initial_screenshot: bool = True,
        log_dir: Optional[str] = None,
        client: Optional[genai.Client] = None,
        input_fn: Callable[[str], str] = input,
    ):
        self._browser_computer = browser_computer
        self._query = query
        self._model_name = model_name
        self._verbose = verbose
        self._api = api
        self._safety_mode = safety_mode
        self._max_steps = max_steps
        self._send_initial_screenshot = send_initial_screenshot
        self._input_fn = input_fn
        self._custom_functions = {f.name: f for f in custom_functions}
        self._environment = environment or getattr(
            browser_computer, "environment", "browser"
        )
        self.final_reasoning: Optional[str] = None
        self.usage: dict[str, int] = {}
        self._step = 0

        self._check_model(model_name)
        if thinking_level:
            self._check_thinking_level(model_name, thinking_level)
        for policy in disabled_safety_policies:
            if policy.lower() not in SAFETY_POLICIES:
                raise ValueError(
                    f"Unknown safety policy '{policy}'. Valid: {', '.join(SAFETY_POLICIES)}"
                )
        if enable_prompt_injection_detection is None:
            info = get_model_info(model_name)
            enable_prompt_injection_detection = (
                info.supports_prompt_injection_detection if info else None
            )

        self._client = client or create_client()
        self._backend_config = BackendConfig(
            model=model_name,
            environment=self._environment,
            excluded_predefined_functions=tuple(excluded_predefined_functions),
            disabled_safety_policies=tuple(disabled_safety_policies),
            enable_prompt_injection_detection=enable_prompt_injection_detection,
            custom_functions=tuple(custom_functions),
            system_instruction=system_instruction,
            thinking_level=thinking_level,
            media_resolution=media_resolution,
            max_recent_screenshots=max_recent_screenshots,
        )
        self._backend = self._make_backend(history)

        self._log_dir: Optional[pathlib.Path] = None
        if log_dir:
            stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            self._log_dir = pathlib.Path(log_dir) / stamp
            self._log_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # setup helpers
    # ------------------------------------------------------------------

    def _make_backend(self, history: str) -> ModelBackend:
        on_retry = lambda attempt, delay, exc: termcolor.cprint(  # noqa: E731
            f"Model request failed on attempt {attempt} ({exc}). Retrying in {delay:.0f}s...",
            color="yellow",
        )
        if self._api == "interactions":
            return InteractionsBackend(
                self._client, self._backend_config, on_retry=on_retry, history=history
            )
        if self._api == "generate_content":
            return GenerateContentBackend(
                self._client, self._backend_config, on_retry=on_retry
            )
        raise ValueError(f"Unknown api: {self._api}")

    @staticmethod
    def _check_model(model_name: str) -> None:
        info = get_model_info(model_name)
        if info is None:
            return
        if info.status == "shutdown":
            termcolor.cprint(
                f"Warning: {model_name} has been shut down ({info.description}). "
                f"Use {info.replacement} instead.",
                color="red",
                attrs=["bold"],
            )
        elif info.status == "deprecated":
            termcolor.cprint(
                f"Warning: {model_name} is deprecated ({info.description}). "
                f"Update to {info.replacement}.",
                color="yellow",
            )

    @staticmethod
    def _check_thinking_level(model_name: str, level: str) -> None:
        if level not in THINKING_LEVELS:
            raise ValueError(
                f"Unknown thinking level '{level}'. Valid: {', '.join(THINKING_LEVELS)}"
            )
        info = get_model_info(model_name)
        if info and info.thinking_levels and level not in info.thinking_levels:
            raise ValueError(
                f"{model_name} does not support thinking_level='{level}'. "
                f"Supported: {', '.join(info.thinking_levels)}"
            )

    # ------------------------------------------------------------------
    # coordinates
    # ------------------------------------------------------------------

    def denormalize_x(self, x: Union[int, float]) -> int:
        width = self._browser_computer.screen_size()[0]
        return max(0, min(width - 1, int(float(x) / 1000 * width)))

    def denormalize_y(self, y: Union[int, float]) -> int:
        height = self._browser_computer.screen_size()[1]
        return max(0, min(height - 1, int(float(y) / 1000 * height)))

    def _xy(self, args: dict[str, Any], x_key: str = "x", y_key: str = "y") -> dict:
        return {"x": self.denormalize_x(args[x_key]), "y": self.denormalize_y(args[y_key])}

    # ------------------------------------------------------------------
    # action dispatch
    # ------------------------------------------------------------------

    def handle_action(
        self, action: Any, use_legacy_actions: Optional[bool] = None
    ) -> FunctionResponseT:
        """Executes one function call and returns the resulting state.

        `action` can be a `ToolCall` or a `google.genai.types.FunctionCall`.
        Both the current (Gemini 3.x / 4) and the legacy action names are
        supported; `use_legacy_actions` is accepted for backwards compatibility
        and ignored.
        """
        name = action.name
        args = dict(getattr(action, "args", None) or {})
        c = self._browser_computer

        if name in self._custom_functions:
            return self._custom_functions[name](args)

        # --- clicks / mouse ------------------------------------------------
        if name in ("click", "click_at"):
            return c.click_at(**self._xy(args))
        if name == "double_click":
            return c.double_click_at(**self._xy(args))
        if name == "triple_click":
            return c.triple_click_at(**self._xy(args))
        if name == "middle_click":
            return c.middle_click_at(**self._xy(args))
        if name == "right_click":
            return c.right_click_at(**self._xy(args))
        if name == "mouse_down":
            return c.mouse_down(**self._xy(args))
        if name == "mouse_up":
            return c.mouse_up(**self._xy(args))
        if name in ("move", "hover_at"):
            return c.hover_at(**self._xy(args))
        if name == "drag_and_drop":
            if "start_x" in args:  # current schema
                start = self._xy(args, "start_x", "start_y")
                end = self._xy(args, "end_x", "end_y")
            else:  # legacy schema
                start = self._xy(args)
                end = self._xy(args, "destination_x", "destination_y")
            return c.drag_and_drop(
                x=start["x"],
                y=start["y"],
                destination_x=end["x"],
                destination_y=end["y"],
            )

        # --- keyboard ------------------------------------------------------
        if name == "type":
            kwargs: dict[str, Any] = {
                "text": args["text"],
                "press_enter": bool(args.get("press_enter", False)),
            }
            if "x" in args and "y" in args:
                kwargs.update(self._xy(args))
            if "clear_before_typing" in args:
                kwargs["clear_before_typing"] = bool(args["clear_before_typing"])
            return c.type_text(**kwargs)
        if name == "type_text_at":
            return c.type_text_at(
                **self._xy(args),
                text=args["text"],
                press_enter=bool(args.get("press_enter", False)),
                clear_before_typing=bool(args.get("clear_before_typing", True)),
            )
        if name == "press_key":
            return c.press_key(args["key"])
        if name in ("hotkey", "key_combination"):
            keys = args["keys"]
            if isinstance(keys, str):
                keys = [k for k in keys.split("+") if k]
            return c.key_combination(list(keys))
        if name == "key_down":
            return c.key_down(args["key"])
        if name == "key_up":
            return c.key_up(args["key"])

        # --- scrolling -----------------------------------------------------
        if name in ("scroll", "scroll_at"):
            direction = args["direction"]
            if direction not in ("up", "down", "left", "right"):
                raise ValueError(f"Unknown scroll direction: {direction}")
            if "magnitude_in_pixels" in args:
                magnitude = int(args["magnitude_in_pixels"])
            elif "magnitude" in args:  # legacy: normalized 0-999
                raw = args["magnitude"]
                magnitude = (
                    self.denormalize_y(raw)
                    if direction in ("up", "down")
                    else self.denormalize_x(raw)
                )
            elif name == "scroll":
                magnitude = 300
            else:
                magnitude = (
                    self.denormalize_y(800)
                    if direction in ("up", "down")
                    else self.denormalize_x(800)
                )
            return c.scroll_at(**self._xy(args), direction=direction, magnitude=magnitude)
        if name == "scroll_document":
            return c.scroll_document(args["direction"])

        # --- misc ----------------------------------------------------------
        if name == "wait":
            seconds = min(float(args.get("seconds", 1)), MAX_WAIT_SECONDS)
            return c.wait(seconds)
        if name == "wait_5_seconds":
            return c.wait_5_seconds()
        if name == "take_screenshot":
            return c.take_screenshot()
        if name == "open_web_browser":
            return c.open_web_browser()

        # --- browser navigation -------------------------------------------
        if name == "navigate":
            return c.navigate(args["url"])
        if name == "go_back":
            return c.go_back()
        if name == "go_forward":
            return c.go_forward()
        if name == "search":
            return c.search()

        if name in ALL_PREDEFINED_FUNCTIONS:
            raise UnsupportedActionError(
                f"'{name}' is not supported by the {self._environment} environment."
            )
        raise ValueError(f"Unsupported function: {name}")

    # ------------------------------------------------------------------
    # safety
    # ------------------------------------------------------------------

    def _get_safety_confirmation(
        self, safety: dict[str, Any]
    ) -> Literal["CONTINUE", "TERMINATE"]:
        decision = str(safety.get("decision", "")).lower()
        explanation = safety.get("explanation", "")
        if decision in ("block", "blocked", "deny", "denied", "prohibited"):
            termcolor.cprint(
                f"Action blocked by the safety service: {explanation}",
                color="red",
                attrs=["bold"],
            )
            return "TERMINATE"
        if decision != "require_confirmation":
            # Allowed / regular decisions need no confirmation.
            return "CONTINUE"

        termcolor.cprint(
            "Safety service requires explicit confirmation!",
            color="yellow",
            attrs=["bold"],
        )
        print(explanation)
        if self._safety_mode == "auto-confirm":
            termcolor.cprint("Auto-confirming (--safety auto-confirm).", color="yellow")
            return "CONTINUE"
        if self._safety_mode == "deny":
            termcolor.cprint("Denied (--safety deny).", color="red")
            return "TERMINATE"
        if not sys.stdin or not sys.stdin.isatty():
            termcolor.cprint(
                "No interactive terminal available to confirm; denying. "
                "Use --safety auto-confirm to approve automatically.",
                color="red",
            )
            return "TERMINATE"
        decision_input = ""
        while decision_input.lower() not in ("y", "n", "ye", "yes", "no"):
            try:
                decision_input = self._input_fn("Do you wish to proceed? [Yes]/[No]\n")
            except EOFError:
                return "TERMINATE"
        return "TERMINATE" if decision_input.lower() in ("n", "no") else "CONTINUE"

    # ------------------------------------------------------------------
    # the loop
    # ------------------------------------------------------------------

    def _execute(self, call: ToolCall) -> ToolResult:
        extra: dict[str, Any] = {}
        safety = call.args.get("safety_decision")
        if isinstance(safety, dict):
            # Asked outside the spinner so the prompt stays readable.
            if self._get_safety_confirmation(safety) == "TERMINATE":
                raise _TerminateLoop("terminated", "Action was not confirmed.")
            if str(safety.get("decision", "")).lower() == "require_confirmation":
                extra["safety_acknowledgement"] = True

        try:
            if call.name in self._custom_functions:
                # Custom functions may prompt the user (yield_to_user): no spinner.
                outcome = self.handle_action(call)
            else:
                outcome = self._with_status(
                    f"Executing {call.name}...", lambda: self.handle_action(call)
                )
        except _TerminateLoop:
            raise
        except Exception as e:  # noqa: BLE001 - report the failure to the model
            termcolor.cprint(f"Action {call.name} failed: {e}", color="red")
            screenshot = None
            payload: dict[str, Any] = {"error": str(e), **extra}
            try:
                state = self._browser_computer.current_state()
                screenshot = state.screenshot
                if state.url:
                    payload["url"] = state.url
            except Exception:  # noqa: BLE001
                pass
            return ToolResult(call=call, payload=payload, screenshot=screenshot, is_error=True)

        if isinstance(outcome, EnvState):
            payload = dict(outcome.metadata)
            if outcome.url is not None:
                payload["url"] = outcome.url
            payload.update(extra)
            return ToolResult(call=call, payload=payload, screenshot=outcome.screenshot)

        payload = dict(outcome)
        payload.update(extra)
        screenshot = None
        fn = self._custom_functions.get(call.name)
        if fn is not None and fn.returns_screenshot:
            state = self._browser_computer.current_state()
            screenshot = state.screenshot
            if state.url:
                payload.setdefault("url", state.url)
        return ToolResult(call=call, payload=payload, screenshot=screenshot)

    def _display(self, turn: ModelTurn) -> None:
        if not self._verbose:
            return
        if turn.thoughts:
            console.print(f"[dim]{turn.thoughts}[/dim]")
        call_strs = []
        intents = []
        for call in turn.calls:
            args = {
                k: v for k, v in call.args.items() if k not in ("intent", "safety_decision")
            }
            s = f"[bold]{call.name}[/bold]"
            if args:
                s += "\n" + "\n".join(f"  {k}: {v}" for k, v in args.items())
            call_strs.append(s)
            if call.args.get("intent"):
                intents.append(f"- {call.args['intent']}")
        reasoning = "\n".join(filter(None, [turn.text, "\n".join(intents)]))
        table = Table(expand=True, title=f"Step {self._step}")
        table.add_column("Gemini Computer Use Reasoning", header_style="magenta", ratio=1)
        table.add_column("Function Call(s)", header_style="cyan", ratio=1)
        table.add_row(reasoning or "", "\n".join(call_strs))
        console.print(table)

    def _log(self, turn: ModelTurn, results: Sequence[ToolResult]) -> None:
        if not self._log_dir:
            return
        record: dict[str, Any] = {
            "step": self._step,
            "time": time.time(),
            "text": turn.text,
            "thoughts": turn.thoughts,
            "status": turn.status,
            "calls": [
                {"id": c.id, "name": c.name, "args": c.args} for c in turn.calls
            ],
            "results": [],
            "usage": turn.usage,
        }
        for i, r in enumerate(results):
            entry: dict[str, Any] = {
                "call_id": r.call.id,
                "name": r.call.name,
                "payload": r.payload,
                "is_error": r.is_error,
            }
            if r.screenshot:
                png = self._log_dir / f"step_{self._step:03d}_{i}_{r.call.name}.png"
                png.write_bytes(r.screenshot)
                entry["screenshot"] = png.name
            record["results"].append(entry)
        with open(self._log_dir / "trajectory.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")

    def _accumulate_usage(self, turn: ModelTurn) -> None:
        # Each request reports its own usage; sum them up over the run.
        for key, value in turn.usage.items():
            self.usage[key] = self.usage.get(key, 0) + value

    def _with_status(self, message: str, fn: Callable[[], Any]) -> Any:
        if self._verbose:
            with console.status(message, spinner_style=None):
                return fn()
        return fn()

    def _next_turn(self, fn: Callable[[], ModelTurn]) -> ModelTurn:
        turn = self._with_status("Generating response from Gemini Computer Use...", fn)
        empty_retries = 0
        while turn.retryable_empty and empty_retries < MAX_EMPTY_RETRIES:
            empty_retries += 1
            self._accumulate_usage(turn)
            turn = self._with_status(
                "Empty/malformed response, retrying...", self._backend.retry
            )
        self._accumulate_usage(turn)
        if turn.retryable_empty:
            turn.error = turn.error or (
                f"The model returned {empty_retries + 1} empty or malformed responses in a row."
            )
        return turn

    def run(self) -> AgentResult:
        """Runs the agent until completion and returns an `AgentResult`."""
        live_url = self._browser_computer.live_view_url()
        if live_url and self._verbose:
            termcolor.cprint(f"Live view: {live_url}", color="green", attrs=["bold"])

        status: AgentStatus = "completed"
        error: Optional[str] = None
        try:
            screenshot = None
            if self._send_initial_screenshot:
                screenshot = self._browser_computer.current_state().screenshot
            turn = self._next_turn(lambda: self._backend.start(self._query, screenshot))

            while True:
                self._step += 1
                if turn.error and not turn.calls:
                    raise _TerminateLoop("error", turn.error)
                if not turn.calls:
                    self.final_reasoning = turn.text
                    self._display(turn)
                    self._log(turn, [])
                    break
                if self._max_steps and self._step > self._max_steps:
                    raise _TerminateLoop(
                        "max_steps", f"Stopped after {self._max_steps} steps (--max_steps)."
                    )
                self._display(turn)
                results = [self._execute(call) for call in turn.calls]
                self._log(turn, results)
                turn = self._next_turn(lambda: self._backend.send_results(results))
        except _TerminateLoop as t:
            status, error = t.status, t.message
        except KeyboardInterrupt:
            status, error = "terminated", "Interrupted by user."
        except Exception as e:  # noqa: BLE001
            status, error = "error", f"{type(e).__name__}: {e}"

        result = AgentResult(
            status=status,
            final_text=self.final_reasoning,
            steps=self._step,
            usage=dict(self.usage),
            log_dir=str(self._log_dir) if self._log_dir else None,
            error=error,
        )
        if self._log_dir:
            (self._log_dir / "result.json").write_text(
                json.dumps(dataclasses.asdict(result), indent=2, default=str)
            )
        if self._verbose:
            self._print_summary(result)
        return result

    def agent_loop(self) -> AgentResult:
        """Backwards compatible alias of `run()`."""
        return self.run()

    @staticmethod
    def _print_summary(result: AgentResult) -> None:
        color = "green" if result.ok else "red"
        termcolor.cprint(
            f"\nAgent finished: {result.status} after {result.steps} step(s).",
            color=color,
            attrs=["bold"],
        )
        if result.final_text:
            print(result.final_text)
        if result.error:
            termcolor.cprint(result.error, color=color)
        if result.usage:
            print(
                "Tokens: "
                + ", ".join(f"{k.replace('total_', '')}={v}" for k, v in result.usage.items())
            )
        if result.log_dir:
            print(f"Run log: {result.log_dir}")


# Backwards compatible name.
BrowserAgent = ComputerUseAgent

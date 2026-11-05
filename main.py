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
"""Command line interface for the Gemini Computer Use agent.

    python main.py --query "Go to Google and search for 'Hello World'"
    python main.py --query_file task.txt     # a long, multi-line task
    python main.py                           # type or paste the task when asked

Every flag also has an environment variable default (see .env.example), so the
same command works locally, in Docker and in CI.
"""
import argparse
import codecs
import os
import signal
import sys
from typing import Optional

from dotenv import load_dotenv

load_dotenv()  # Load environment variables from .env file

from agent import ComputerUseAgent, create_client  # noqa: E402
from computers import (  # noqa: E402
    BrowserbaseComputer,
    BrowserUseComputer,
    CDPComputer,
    DesktopComputer,
    PlaywrightComputer,
)
from custom_functions import YIELD_TO_USER  # noqa: E402
from models import (  # noqa: E402
    DEFAULT_MODEL,
    MEDIA_RESOLUTIONS,
    MODELS,
    SAFETY_POLICIES,
    THINKING_LEVELS,
)

PLAYWRIGHT_SCREEN_SIZE = (1440, 900)

ENVIRONMENTS = ("playwright", "cdp", "browserbase", "browser-use", "desktop")
ENV_ALIASES = {"browseruse": "browser-use", "browser_use": "browser-use", "local": "playwright"}


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _env_bool(name: str) -> Optional[bool]:
    value = _env(name)
    if value is None:
        return None
    return value.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    value = _env(name)
    return int(value) if value is not None else default


def _csv(value: Optional[str]) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def parse_screen_size(value: str) -> tuple[int, int]:
    try:
        width, height = value.lower().replace(" ", "").split("x")
        return int(width), int(height)
    except ValueError:
        raise argparse.ArgumentTypeError(f"Screen size must look like 1440x900, got {value!r}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the Gemini Computer Use agent with a query.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    task = parser.add_mutually_exclusive_group()
    task.add_argument(
        "--query",
        type=str,
        help="The task for the agent. Leave out --query and --query_file to type "
        "or paste it (multi-line is fine) when asked, or pipe it in.",
    )
    task.add_argument(
        "--query_file",
        "--query-file",
        dest="query_file",
        help="Read the task from this text file (UTF-8 or UTF-16).",
    )
    parser.add_argument(
        "--env",
        type=str,
        default=_env("COMPUTER_USE_ENV", "playwright"),
        help=f"Where the agent acts: {', '.join(ENVIRONMENTS)}.",
    )
    parser.add_argument(
        "--model",
        default=_env("GEMINI_MODEL", DEFAULT_MODEL),
        help=f"Gemini model. Known: {', '.join(MODELS)}.",
    )
    parser.add_argument(
        "--initial_url",
        "--initial-url",
        dest="initial_url",
        type=str,
        default=_env("INITIAL_URL", "https://www.google.com"),
        help="The initial URL loaded in the browser.",
    )
    parser.add_argument(
        "--highlight_mouse",
        "--highlight-mouse",
        dest="highlight_mouse",
        action="store_true",
        default=False,
        help="Highlight the mouse position in the browser (visual debugging).",
    )

    g = parser.add_argument_group("Gemini Computer Use")
    g.add_argument(
        "--api",
        choices=("interactions", "generate_content"),
        default=_env("GEMINI_API", "interactions"),
        help="Interactions API (recommended) or the legacy generateContent API.",
    )
    g.add_argument(
        "--history",
        choices=("server", "client"),
        default=_env("GEMINI_HISTORY", "server"),
        help="Interactions API only. 'server' chains turns with previous_interaction_id; "
        "'client' sends the history each turn (store=false) and drops old screenshots.",
    )
    g.add_argument("--thinking_level", choices=THINKING_LEVELS, default=_env("GEMINI_THINKING_LEVEL"))
    g.add_argument(
        "--media_resolution",
        choices=MEDIA_RESOLUTIONS,
        default=_env("GEMINI_MEDIA_RESOLUTION"),
        help="Resolution used for screenshots sent to the model.",
    )
    g.add_argument(
        "--prompt_injection_detection",
        action=argparse.BooleanOptionalAction,
        default=_env_bool("GEMINI_PROMPT_INJECTION_DETECTION"),
        help="Scan screenshots for prompt injections. Default: on for supported models.",
    )
    g.add_argument(
        "--disable_safety_policy",
        action="append",
        choices=SAFETY_POLICIES,
        default=_csv(_env("GEMINI_DISABLED_SAFETY_POLICIES")) or None,
        help="Disable (as a preference) a safety policy category. Repeatable.",
    )
    g.add_argument(
        "--exclude_action",
        action="append",
        default=_csv(_env("GEMINI_EXCLUDED_ACTIONS")) or None,
        help="Exclude a predefined Computer Use action, e.g. drag_and_drop. Repeatable.",
    )
    g.add_argument("--system_instruction", default=_env("GEMINI_SYSTEM_INSTRUCTION"))
    g.add_argument(
        "--system_instruction_file",
        default=_env("GEMINI_SYSTEM_INSTRUCTION_FILE"),
        help="Read the system instruction from a file.",
    )
    g.add_argument(
        "--safety",
        choices=("ask", "auto-confirm", "deny"),
        default=_env("SAFETY_CONFIRMATION", "ask"),
        help="How to answer 'require_confirmation' safety decisions. 'ask' prompts in "
        "the terminal (denies if there is no terminal).",
    )
    g.add_argument(
        "--hitl",
        action=argparse.BooleanOptionalAction,
        default=bool(_env_bool("HUMAN_IN_THE_LOOP")),
        help="Offer the model a yield_to_user function to ask you for input (2FA codes, ...).",
    )
    g.add_argument("--max_steps", type=int, default=_env_int("MAX_STEPS", 100), help="0 = unlimited.")
    g.add_argument(
        "--max_screenshots",
        type=int,
        default=_env_int("MAX_RECENT_SCREENSHOTS", 3),
        help="Screenshots kept in client-side history (generate_content / --history client).",
    )
    g.add_argument("--log_dir", default=_env("RUN_LOG_DIR"), help="Save a trajectory + screenshots here.")
    g.add_argument("--quiet", action="store_true", help="Only print the final result.")

    b = parser.add_argument_group("Browser")
    b.add_argument(
        "--screen_size",
        type=parse_screen_size,
        default=parse_screen_size(_env("SCREEN_SIZE", "1440x900")),
        help="Browser viewport WIDTHxHEIGHT (1440x900 recommended).",
    )
    b.add_argument(
        "--headless",
        action=argparse.BooleanOptionalAction,
        default=_env_bool("PLAYWRIGHT_HEADLESS"),
        help="Run the local browser headless (default: headless only without a display).",
    )
    b.add_argument("--browser_channel", default=_env("PLAYWRIGHT_CHANNEL"), help="chromium, chrome, msedge ...")
    b.add_argument("--user_data_dir", default=_env("BROWSER_USER_DATA_DIR"), help="Persistent browser profile.")
    b.add_argument("--proxy_server", default=_env("BROWSER_PROXY_SERVER"), help="Local browser proxy.")
    b.add_argument(
        "--cdp_url",
        default=_env("CDP_URL"),
        help="For --env cdp: a CDP URL, or the name of your running browser (chrome, "
        "msedge, ...; needs chrome://inspect/#remote-debugging). Default: chrome.",
    )
    b.add_argument("--allowed_domains", default=_env("ALLOWED_DOMAINS"), help="Comma separated allowlist.")
    b.add_argument("--blocked_domains", default=_env("BLOCKED_DOMAINS"), help="Comma separated blocklist.")
    b.add_argument(
        "--tab_mode",
        choices=("single", "follow"),
        default=_env("TAB_MODE", "single"),
        help="'single' opens new tabs in the current tab; 'follow' switches to new tabs.",
    )
    return parser


def build_computer(args: argparse.Namespace):
    env = ENV_ALIASES.get(args.env, args.env)
    if env == "desktop":
        return DesktopComputer(screen_size=args.screen_size)

    browser_kwargs = dict(
        allowed_domains=_csv(args.allowed_domains),
        blocked_domains=_csv(args.blocked_domains),
        tab_mode=args.tab_mode,
    )
    if env == "playwright":
        return PlaywrightComputer(
            screen_size=args.screen_size,
            initial_url=args.initial_url,
            highlight_mouse=args.highlight_mouse,
            headless=args.headless,
            browser_channel=args.browser_channel,
            user_data_dir=args.user_data_dir,
            proxy_server=args.proxy_server,
            **browser_kwargs,
        )
    if env == "cdp":
        return CDPComputer(
            args.cdp_url,
            screen_size=args.screen_size,
            initial_url=args.initial_url,
            highlight_mouse=args.highlight_mouse,
            **browser_kwargs,
        )
    if env == "browserbase":
        return BrowserbaseComputer(
            screen_size=args.screen_size,
            initial_url=args.initial_url,
            highlight_mouse=args.highlight_mouse,
            **browser_kwargs,
        )
    if env == "browser-use":
        return BrowserUseComputer(
            screen_size=args.screen_size,
            initial_url=args.initial_url,
            highlight_mouse=args.highlight_mouse,
            **browser_kwargs,
        )
    raise ValueError(f"Unknown environment: {args.env}. Choose one of {', '.join(ENVIRONMENTS)}.")


def _decode_text(data: bytes, source: str) -> str:
    # Editors save UTF-8; Windows PowerShell 5.1's `>` and Out-File save UTF-16.
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        text = data.decode("utf-16")
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ValueError(f"{source} is not UTF-8 text; save it as UTF-8 and try again.") from None
    return text.replace("\r\n", "\n").replace("\r", "\n")


def resolve_query(args: argparse.Namespace, stdin=None) -> str:
    """The task: --query, else --query_file, else read from stdin (typed or
    pasted in a terminal, or piped). Text arrives exactly as written,
    newlines and quotes included."""
    stdin = stdin if stdin is not None else sys.stdin
    if args.query is not None:
        query = args.query
    elif args.query_file:
        with open(args.query_file, "rb") as f:
            query = _decode_text(f.read(), args.query_file)
    else:
        if stdin is None:
            raise ValueError('No task given. Use --query "..." or --query_file FILE.')
        if stdin.isatty():
            done = "Ctrl+Z, then Enter" if sys.platform == "win32" else "Ctrl+D"
            print(f"Type or paste the task. When done, press Enter, then {done}:", flush=True)
        query = stdin.read()
    query = query.lstrip("﻿").strip()  # PowerShell pipes a byte order mark
    if not query:
        raise ValueError('The task is empty. Use --query "...", --query_file FILE, or type it when asked.')
    return query


def _system_instruction(args: argparse.Namespace) -> Optional[str]:
    if args.system_instruction_file:
        with open(args.system_instruction_file, encoding="utf-8") as f:
            return f.read()
    return args.system_instruction


def run_gemini(args: argparse.Namespace) -> int:
    client = create_client()  # fails fast on a missing key, before a browser starts
    args.query = resolve_query(args)  # asks for the task before a browser starts
    env = build_computer(args)
    custom_functions = [YIELD_TO_USER] if args.hitl else []
    with env as computer:
        agent = ComputerUseAgent(
            browser_computer=computer,
            query=args.query,
            model_name=args.model,
            verbose=not args.quiet,
            client=client,
            api=args.api,
            history=args.history,
            thinking_level=args.thinking_level,
            media_resolution=args.media_resolution,
            enable_prompt_injection_detection=args.prompt_injection_detection,
            disabled_safety_policies=args.disable_safety_policy or (),
            excluded_predefined_functions=args.exclude_action or (),
            custom_functions=custom_functions,
            system_instruction=_system_instruction(args),
            safety_mode=args.safety,
            max_steps=args.max_steps,
            max_recent_screenshots=args.max_screenshots,
            log_dir=args.log_dir,
        )
        result = agent.agent_loop()
    if args.quiet:
        print(result.final_text or result.error or result.status)
    return 0 if result.ok else 1


def _install_sigterm_handler() -> None:
    """Treat SIGTERM (docker stop, CI cancel, timeout) like Ctrl+C so cloud
    browser sessions are released / stopped instead of left running (billed)."""

    def handler(signum, frame):
        raise KeyboardInterrupt()

    try:
        signal.signal(signal.SIGTERM, handler)
    except ValueError:  # not the main thread
        pass


def main(argv: Optional[list[str]] = None) -> int:
    _install_sigterm_handler()
    args = build_parser().parse_args(argv)
    args.env = ENV_ALIASES.get(args.env, args.env)
    if args.env not in ENVIRONMENTS:
        raise SystemExit(f"Unknown --env {args.env!r}. Choose one of {', '.join(ENVIRONMENTS)}.")
    try:
        return run_gemini(args)
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except (ValueError, RuntimeError, PermissionError, FileNotFoundError) as e:
        # Setup problems (missing API key, no display, bad option, ...).
        print(f"Error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

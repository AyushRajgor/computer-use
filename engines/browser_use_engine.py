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
"""Optional engine: the open-source `browser-use` agent, powered by Gemini.

Unlike the default engine (pure Gemini Computer Use: screenshots + pixel
coordinates), browser-use mostly works from the page's DOM / element indexes,
with screenshots as extra context. Gemini still makes every decision.

Install (browser-use pins an older google-genai, so overrides are required):

    ./scripts/install_engines.sh

Usage:

    python main.py --engine browser-use --query "..."               # local Chrome
    python main.py --engine browser-use --env browser-use --query "..."  # Browser Use Cloud browser
    python main.py --engine browser-use --env browserbase --query "..."  # Browserbase browser
    python main.py --engine browser-use --env cdp --cdp_url http://localhost:9222 --query "..."
"""
import asyncio
import os
from typing import Optional, Sequence

from agent import AgentResult, _env_flag

INSTALL_HINT = (
    "The browser-use engine is not installed. Run ./scripts/install_engines.sh "
    "(or see requirements-engines.txt)."
)


def _make_llm(model: str, thinking_level: Optional[str]):
    from browser_use import ChatGoogle

    kwargs = {"model": model}
    if thinking_level:
        kwargs["thinking_level"] = thinking_level
    if _env_flag("USE_VERTEXAI") or _env_flag("GOOGLE_GENAI_USE_ENTERPRISE"):
        kwargs.update(
            vertexai=True,
            project=os.environ.get("VERTEXAI_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT"),
            location=os.environ.get("VERTEXAI_LOCATION")
            or os.environ.get("GOOGLE_CLOUD_LOCATION")
            or "global",
        )
    else:
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("Set GEMINI_API_KEY (or GOOGLE_API_KEY) for the browser-use engine.")
        kwargs["api_key"] = api_key
    return ChatGoogle(**kwargs)


async def _run(
    task: str,
    *,
    model: str,
    cdp_url: Optional[str],
    headless: Optional[bool],
    screen_size: tuple[int, int],
    max_steps: int,
    initial_url: Optional[str],
    user_data_dir: Optional[str],
    browser_channel: Optional[str],
    allowed_domains: Sequence[str],
    blocked_domains: Sequence[str],
    thinking_level: Optional[str],
    flash_mode: bool,
) -> AgentResult:
    from browser_use import Agent, Browser

    browser_kwargs = {}
    if cdp_url:
        # keep_alive: the provider session is stopped by our own code.
        browser_kwargs.update(cdp_url=cdp_url, keep_alive=True)
    else:
        browser_kwargs.update(
            viewport={"width": screen_size[0], "height": screen_size[1]},
            window_size={"width": screen_size[0], "height": screen_size[1]},
        )
        if headless is not None:
            browser_kwargs["headless"] = headless
        if user_data_dir:
            browser_kwargs["user_data_dir"] = user_data_dir
        if browser_channel:
            browser_kwargs["channel"] = browser_channel
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            browser_kwargs["chromium_sandbox"] = False  # Chrome refuses the sandbox as root (Docker)
    if allowed_domains:
        browser_kwargs["allowed_domains"] = list(allowed_domains)
    if blocked_domains:
        browser_kwargs["prohibited_domains"] = list(blocked_domains)
    browser = Browser(**browser_kwargs)

    agent_kwargs = dict(
        task=task,
        llm=_make_llm(model, thinking_level),
        browser=browser,
        use_vision=True,
        use_judge=False,
        flash_mode=flash_mode,
        enable_signal_handler=False,
    )
    if initial_url:
        agent_kwargs["initial_actions"] = [{"navigate": {"url": initial_url, "new_tab": False}}]
    agent = Agent(**agent_kwargs)
    try:
        history = await agent.run(max_steps=max_steps or 500)
    finally:
        try:
            if cdp_url:
                await browser.stop()  # disconnect only; the provider session is ours to stop
            else:
                await browser.kill()  # local Chrome launched by browser-use
        except Exception:  # noqa: BLE001 - already closed
            pass

    errors = [e for e in history.errors() if e]
    if history.is_done():
        status = "completed" if history.is_successful() is not False else "error"
    else:
        status = "max_steps" if history.number_of_steps() >= (max_steps or 500) else "error"
    usage = {}
    raw_usage = getattr(history, "usage", None)
    for key in ("total_prompt_tokens", "total_completion_tokens", "total_tokens"):
        value = getattr(raw_usage, key, None)
        if isinstance(value, int):
            usage[key] = value
    return AgentResult(
        status=status,
        final_text=history.final_result(),
        steps=history.number_of_steps(),
        usage=usage,
        error=None if status == "completed" else ("; ".join(errors[-3:]) or None),
    )


def run_browser_use(task: str, **kwargs) -> AgentResult:
    """Runs `task` with the browser-use agent. See `_run` for keyword arguments."""
    os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")
    os.environ.setdefault("BROWSER_USE_SETUP_LOGGING", "false")
    try:
        import browser_use  # noqa: F401
    except ImportError as e:
        raise SystemExit(f"{INSTALL_HINT} ({e})")
    return asyncio.run(_run(task, **kwargs))

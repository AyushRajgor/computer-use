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
"""report.html: one session as a page to read in a browser.

The agent writes it into the session's folder when a run ends (completed,
failed or stopped) and prints a link to it; `python runs.py <session> --html`
rebuilds it, for example for a session that was killed.

The page is a single file next to the session's screenshots, which it links
to by name, so the folder can be zipped and shared as is. Everything in it
that came from the model or a web page is escaped, and a Content Security
Policy only lets the page's own script run.
"""
import datetime
import html
import json
import os
import pathlib
import secrets
import urllib.parse
import webbrowser
from typing import Any, Optional

from rich.style import Style
from rich.text import Text

from models import PRICES_URL, Cost, estimate_cost, priced_model
from telemetry import (
    REPORT_FILE,
    RESULT_FILE,
    RUN_FILE,
    RUNS_DIR,
    STEPS_FILE,
    Session,
    format_duration,
    format_number,
    format_seconds,
    one_line,
    parse_timestamp,
)

# status -> (label, tone, icon)
STATUSES = {
    "completed": ("Completed", "ok", "check"),
    "error": ("Failed", "bad", "x"),
    "terminated": ("Stopped", "warn", "stop"),
    "max_steps": ("Step limit reached", "warn", "stop"),
    "unfinished": ("Unfinished", "idle", "hourglass"),
}
# A status the agent doesn't write today (an older or newer version) still
# gets a page: a neutral badge with the status as its label.
UNKNOWN_STATUS = ("idle", "alert")

# What the agent's stop messages mean (agent.py raises them as _TerminateLoop
# or on KeyboardInterrupt, which main.py also raises on SIGTERM).
STOP_REASONS = {
    "Interrupted by user.": (
        "Ctrl+C was pressed, or the process got SIGTERM (docker stop, a CI cancel or timeout)."
    ),
    "Action was not confirmed.": (
        "Gemini asked for a safety confirmation and it was declined (or there was no terminal "
        "to ask in), so the action was not run."
    ),
}

# `stopped` in a step -> what happened in that step
STOPPED_IN_STEP = {
    "error": "The run failed in this step.",
    "terminated": "The run was stopped in this step.",
    "max_steps": "The step limit was reached: the actions below were not run.",
}

ENVIRONMENTS = {
    "playwright": "Playwright browser",
    "cdp": "Your browser (CDP)",
    "browserbase": "Browserbase",
    "browser-use": "Browser Use",
    "desktop": "Desktop",
    "browser": "Browser",
}

_LIBRARIES = {"python": "Python"}

SCREENSHOT_TYPES = (".png", ".jpg", ".jpeg", ".webp")
PNG_SIGNATURE = bytes([0x89]) + b"PNG\r\n" + bytes([0x1A]) + b"\n"

# Inline icons (24x24, stroked), so the page needs nothing from the network.
_ICONS = {
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "x": '<path d="M18 6 6 18M6 6l12 12"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="2"/>',
    "hourglass": '<path d="M6 3h12M6 21h12M7 3v3.5a5 5 0 0 0 10 0V3M7 21v-3.5a5 5 0 0 1 10 0V21"/>',
    "alert": '<path d="M12 9v4M12 17h.01"/><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>',
    "globe": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
    "pointer": '<path d="m5 3 6.5 17 2.4-7.1L21 10.5z"/>',
    "keyboard": '<rect x="2" y="6" width="20" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10"/>',
    "scroll": '<path d="M12 4v16M8 8l4-4 4 4M8 16l4 4 4-4"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "camera": '<path d="M4 8h3l2-3h6l2 3h3a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1z"/><circle cx="12" cy="13" r="3.5"/>',
    "back": '<path d="M19 12H5M12 19l-7-7 7-7"/>',
    "forward": '<path d="M5 12h14M12 5l7 7-7 7"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    "drag": '<path d="M5 9l-3 3 3 3M9 5l3-3 3 3M15 19l-3 3-3-3M19 9l3 3-3 3M2 12h20M12 2v20"/>',
    "user": '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
    "bolt": '<path d="M13 2 3 14h9l-1 8 10-12h-9z"/>',
    "message": '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
    "copy": '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1"/>',
    "left": '<path d="m15 18-6-6 6-6"/>',
    "right": '<path d="m9 18 6-6-6-6"/>',
    "external": '<path d="M14 3h7v7M10 14 21 3M21 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5"/>',
    "folder": '<path d="M3 6a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "file": '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6"/>',
    "coins": '<circle cx="8" cy="8" r="6"/><path d="M18.09 10.37A6 6 0 1 1 10.34 18M7 6h1v4M16.71 13.88l.7.71-2.82 2.82"/>',
    "list": '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
    "hash": '<path d="M4 9h16M4 15h16M10 3 8 21M16 3l-2 18"/>',
    "cpu": '<rect x="5" y="5" width="14" height="14" rx="2"/><path d="M9 9h6v6H9zM9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3"/>',
    "monitor": '<rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
}


def _icon(name: str) -> str:
    return (
        '<svg class="i" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
        f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{_ICONS[name]}</svg>'
    )


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _json(value: Any) -> str:
    return _e(json.dumps(value, indent=2, ensure_ascii=False, default=str))


def _when(moment: Optional[datetime.datetime]) -> str:
    return f"{moment:%b} {moment.day}, {moment:%Y} at {moment:%H:%M:%S}" if moment else "-"


# ---------------------------------------------------------------------------
# time: the clock times written in the logs
# ---------------------------------------------------------------------------
#
# The logs are written in the computer's local time: ISO timestamps carry
# their UTC offset, `time` fields are Unix times. The page shows the clock
# times as they were written, so a Unix time is moved to the run's offset
# rather than to the time zone of whoever opens the page.


def _tz(session: Session) -> Optional[datetime.tzinfo]:
    start = session.started_at
    return start.tzinfo if start else None


def _moment(value: Any, tz: Optional[datetime.tzinfo]) -> Optional[datetime.datetime]:
    moment = parse_timestamp(value)
    if moment and tz is not None and isinstance(value, (int, float)):
        moment = moment.astimezone(tz)
    return moment


def _clock(moment: Optional[datetime.datetime]) -> str:
    return f"{moment:%H:%M:%S}" if moment else ""


def _offset(start: Optional[datetime.datetime], moment: Optional[datetime.datetime]) -> str:
    """'+1:05': how long after `start` `moment` was."""
    if not (start and moment):
        return ""
    try:
        seconds = (moment - start).total_seconds()
    except TypeError:  # one of them has no offset (an older log)
        return ""
    minutes, secs = divmod(int(max(0.0, seconds)), 60)
    return f"+{minutes}:{secs:02d}"


def _run_times(
    session: Session, steps: list[dict[str, Any]], tz: Optional[datetime.tzinfo]
) -> tuple[Optional[datetime.datetime], Optional[datetime.datetime]]:
    """When the run started and ended: from result.json, else from the steps."""
    result = session.result or {}
    start = session.started_at or _moment(result.get("started_at"), tz)
    if start is None and steps:
        start = _moment(steps[0].get("started_at"), tz)
    end = _moment(result.get("ended_at"), tz)
    if end is None and steps:
        end = _moment(steps[-1].get("time"), tz)
    return start, end


# ---------------------------------------------------------------------------
# what each step did, in words
# ---------------------------------------------------------------------------


def _typed(args: dict[str, Any]) -> str:
    text = f"Type “{one_line(str(args.get('text', '')), 60)}”"
    return text + (" and press Enter" if args.get("press_enter") else "")


def _keys(args: dict[str, Any]) -> str:
    keys = args.get("keys", "")
    return "+".join(str(k) for k in keys) if isinstance(keys, list) else str(keys)


# action name -> (icon, label or label(args))
_ACTIONS: dict[str, tuple[str, Any]] = {
    "navigate": ("globe", lambda a: f"Go to {a.get('url', 'a page')}"),
    "open_web_browser": ("globe", "Open the browser"),
    "search": ("search", "Open a search page"),
    "go_back": ("back", "Go back"),
    "go_forward": ("forward", "Go forward"),
    "click": ("pointer", "Click"),
    "click_at": ("pointer", "Click"),
    "double_click": ("pointer", "Double-click"),
    "triple_click": ("pointer", "Triple-click"),
    "right_click": ("pointer", "Right-click"),
    "middle_click": ("pointer", "Middle-click"),
    "hover_at": ("pointer", "Hover"),
    "move": ("pointer", "Move the mouse"),
    "mouse_down": ("pointer", "Press the mouse button"),
    "mouse_up": ("pointer", "Release the mouse button"),
    "drag_and_drop": ("drag", "Drag and drop"),
    "type": ("keyboard", _typed),
    "type_text_at": ("keyboard", _typed),
    "press_key": ("keyboard", lambda a: f"Press {a.get('key', 'a key')}"),
    "key_combination": ("keyboard", lambda a: f"Press {_keys(a)}"),
    "hotkey": ("keyboard", lambda a: f"Press {_keys(a)}"),
    "key_down": ("keyboard", lambda a: f"Hold down {a.get('key', 'a key')}"),
    "key_up": ("keyboard", lambda a: f"Let go of {a.get('key', 'a key')}"),
    "scroll": ("scroll", lambda a: f"Scroll {a.get('direction', '')}".strip()),
    "scroll_at": ("scroll", lambda a: f"Scroll {a.get('direction', '')}".strip()),
    "scroll_document": ("scroll", lambda a: f"Scroll the page {a.get('direction', '')}".strip()),
    "wait": ("clock", lambda a: f"Wait {a.get('seconds', 1)}s"),
    "wait_5_seconds": ("clock", "Wait 5s"),
    "take_screenshot": ("camera", "Take a screenshot"),
    "yield_to_user": ("user", "Ask you for help"),
}


def describe_action(name: str, args: dict[str, Any]) -> tuple[str, str]:
    """(icon, a short label in words) for a tool call, e.g. ('keyboard', 'Press Enter')."""
    icon, label = _ACTIONS.get(name, ("bolt", (name or "action").replace("_", " ").capitalize()))
    if callable(label):
        try:
            label = label(args)
        except Exception:  # noqa: BLE001 - odd arguments from the model
            label = name
    return icon, label


def _signature(name: str, args: dict[str, Any]) -> str:
    """navigate(url="https://example.com") — the call as the model made it."""
    shown = []
    for key, value in args.items():
        text = json.dumps(value, ensure_ascii=False, default=str)
        shown.append(f"{key}={one_line(text, 80)}")
    return f"{name}({', '.join(shown)})"


def _screenshot(name: Any, session: Session) -> Optional[str]:
    """The screenshot's file name if it is a picture inside the session folder."""
    if not (isinstance(name, str) and pathlib.PurePath(name).name == name):
        return None
    if not name.lower().endswith(SCREENSHOT_TYPES) or not (session.path / name).is_file():
        return None
    return name


def _png_size(path: pathlib.Path) -> Optional[tuple[int, int]]:
    """(width, height) from a PNG's header, so the page can keep room for the
    picture before it loads (they load lazily) and doesn't jump around."""
    try:
        with open(path, "rb") as f:
            head = f.read(24)
    except OSError:
        return None
    if len(head) < 24 or not head.startswith(PNG_SIGNATURE):
        return None
    return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")


def _img(name: str, size: Optional[tuple[int, int]], alt: str, *, eager: bool = False) -> str:
    dims = f' width="{size[0]}" height="{size[1]}"' if size else ""
    src = _e(urllib.parse.quote(name))
    loading = 'loading="eager" fetchpriority="high"' if eager else 'loading="lazy"'
    return f'<img {loading} decoding="async" src="{src}"{dims} alt="{_e(alt)}">'


def _actions(step: dict[str, Any], session: Session, tz: Optional[datetime.tzinfo]) -> list[dict[str, Any]]:
    results = {r.get("call_id"): r for r in step.get("results") or []}
    actions = []
    for call in step.get("calls") or []:
        args = dict(call.get("args") or {})
        intent = args.pop("intent", None)
        args.pop("safety_decision", None)  # the safety service's verdict, not an argument
        name = str(call.get("name") or "")
        icon, label = describe_action(name, args)
        result = results.get(call.get("id"))
        payload = (result or {}).get("payload")
        failed = bool(result and result.get("is_error"))
        error = payload.get("error") if failed and isinstance(payload, dict) else None
        screenshot = _screenshot((result or {}).get("screenshot"), session)
        actions.append(
            {
                "name": name,
                "icon": icon,
                "label": label,
                "title": intent or label,
                "signature": _signature(name, args),
                "args": args,
                "result": result,
                "failed": failed,
                "error": error,
                "duration": (result or {}).get("duration_s"),
                "screenshot": screenshot,
                "size": _png_size(session.path / screenshot) if screenshot else None,
                "began": _moment((result or {}).get("started_at"), tz),
                "ended": _moment((result or {}).get("ended_at"), tz),
            }
        )
    # Logs from before each action's times were saved: the step's `time` is
    # taken right after its actions ran, so the last action ended then and each
    # earlier one that much sooner.
    end = _moment(step.get("time"), tz)
    later = 0.0
    for action in reversed(actions):
        duration = action["duration"]
        if not isinstance(duration, (int, float)):
            continue
        if action["ended"] is None and end:
            action["ended"] = end - datetime.timedelta(seconds=later)
        if action["began"] is None and action["ended"]:
            action["began"] = action["ended"] - datetime.timedelta(seconds=duration)
        later += duration
    return actions


def _result_lines(action: dict[str, Any]) -> list[tuple[str, str, str]]:
    """What the agent sent back to Gemini after the action, as (kind, label,
    text) rows; kind is 'error', 'url', 'text' or 'muted' (a muted row has no
    label).

    The payload shapes come from agent.py: `{"url": ...}` plus the computer's
    metadata (desktop: `active_window`) after a browser action,
    `{"error": ...}` (and the URL if it could still be read) when the action
    raised, `"safety_acknowledgement": true` when you approved a safety
    confirmation, and whatever a custom function returned (`yield_to_user`:
    `user_response`, or `error` when nobody could answer)."""
    result = action["result"]
    if result is None:
        return [("muted", "", "Not run: the run stopped before this action.")]
    payload = result.get("payload")
    if not isinstance(payload, dict):
        if payload in (None, "", [], {}):
            return [("muted", "", "Only the screenshot." if action["screenshot"] else "Nothing.")]
        return [("text", "Returned", one_line(json.dumps(payload, ensure_ascii=False, default=str), 300))]
    rest = dict(payload)
    lines = []
    error = rest.pop("error", None)
    if action["failed"]:
        lines.append(("error", "Failed", str(error or "the action raised an error")))
    elif error:  # a custom function that answered with an error, e.g. yield_to_user without a terminal
        lines.append(("error", "Error reported", str(error)))
    url = rest.pop("url", None)
    if url:
        lines.append(("url", "Page after", str(url)))
    window = rest.pop("active_window", None)
    if window:
        lines.append(("text", "Active window", str(window)))
    if rest.pop("safety_acknowledgement", None):
        lines.append(("text", "Safety confirmation", "you (or --safety auto-confirm) approved this action."))
    if "user_response" in rest:
        reply = rest.pop("user_response")
        lines.append(("text", "Your reply", str(reply)) if reply else ("muted", "", "No reply from you."))
    for key, value in rest.items():
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
        lines.append(("text", str(key), one_line(text, 300)))
    if not lines:
        lines.append(("muted", "", "Only the screenshot." if action["screenshot"] else "Nothing."))
    return lines


def _arg_value(value: Any) -> str:
    """An argument as the model sent it: strings bare, everything else as JSON."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return one_line(text, 300)


def _step_views(
    steps: list[dict[str, Any]], session: Session, tz: Optional[datetime.tzinfo]
) -> list[dict[str, Any]]:
    start = session.started_at
    model = str(session.run.get("model") or "")
    day = start.date() if start else None
    views = []
    for step in steps:
        actions = _actions(step, session, tz)
        stopped = step.get("stopped")
        problem = step.get("error") or (
            STOPPED_IN_STEP.get(stopped, f"The run stopped in this step ({stopped}).") if stopped else None
        )
        if actions:
            title = actions[0]["title"] + (f" (+{len(actions) - 1} more)" if len(actions) > 1 else "")
            kind = "failed" if problem or any(a["failed"] for a in actions) else "action"
        elif problem:
            title, kind = "Stopped here", "failed"
        else:
            title, kind = "Final answer", "final"
        began, ended = _moment(step.get("started_at"), tz), _moment(step.get("time"), tz)
        seconds = (step.get("model_s") or 0) + (step.get("actions_s") or 0)
        if not seconds and began and ended:
            try:
                seconds = max(0.0, (ended - began).total_seconds())
            except TypeError:
                seconds = 0
        views.append(
            {
                "step": step,
                "number": step.get("step"),
                "actions": actions,
                "problem": problem,
                "title": title,
                "kind": kind,
                "seconds": seconds,
                "began": began,
                "ended": ended,
                "offset": _offset(start, began),
                "cost": estimate_cost(model, step.get("usage") or {}, day) if model else None,
            }
        )
    return views


# ---------------------------------------------------------------------------
# page sections
# ---------------------------------------------------------------------------


def _status(status: str) -> tuple[str, str, str]:
    """(label, tone, icon) for a status, with a neutral fallback for one the
    page doesn't know."""
    if status in STATUSES:
        return STATUSES[status]
    return (status.replace("_", " ").capitalize() or "Unknown", *UNKNOWN_STATUS)


def _outcome(session: Session, views: list[dict[str, Any]]) -> str:
    result = session.result or {}
    status = session.status
    label, tone, icon = _status(status)
    # The step the run stopped in, if it was logged as such; otherwise it
    # stopped after the last logged step (e.g. on the next request to Gemini).
    stop = next((v for v in reversed(views) if v["problem"]), None)
    where = f" at step {stop['number']}" if stop else ""
    if stop is None and views:
        stop = views[-1]
        where = f" after step {stop['number']}"
    if not views:
        where = " before the first step"
    error = result.get("error")
    answer = result.get("final_text")
    note = None
    mono = False

    if status == "completed":
        heading = "The agent finished the task"
        body = answer or "It finished without a final message."
        where = ""
    elif status == "unfinished":
        heading = "No result yet"
        body = (
            "The agent is still running, or it stopped before it could save how the run ended "
            "(for example, the process was killed). Below is everything it logged."
        )
        where = ""
    elif status == "error":
        heading = "The run failed" + where
        body = error or "No error message was saved."
        mono = bool(error)
    elif status == "terminated":
        heading = "The run was stopped" + where
        body = error or "The run was stopped before it finished."
        note = STOP_REASONS.get(error)
    elif status == "max_steps":
        heading = "The run hit its step limit" + where
        body = error or "The step limit was reached before the task was done."
        note = "Gemini still had actions to run when the limit was reached. Raise --max_steps or give it a smaller task."
    else:
        heading = f"The run ended with status “{status}”" + where
        body = error or "This report doesn't know that status; below is everything the agent logged."
        mono = bool(error)

    buttons = []
    if stop and where and views:
        buttons.append(f'<a class="btn" href="#step-{_e(stop["number"])}">Go to step {_e(stop["number"])}</a>')
    if status != "unfinished" and (answer or error):
        what = "answer" if status == "completed" else "error" if mono else "message"
        buttons.append(_copy_button("outcome-text", f"Copy {what}"))
    out = [
        f'<section class="outcome tone-{tone}" aria-labelledby="outcome-title">',
        f'<div class="outcome-top"><span class="overline">Session result</span><span class="badge">{_icon(icon)}{_e(label)}</span></div>',
        f'<h2 id="outcome-title">{_e(heading)}</h2>',
        f'<div id="outcome-text" class="{"outcome-mono" if mono else "outcome-prose"}">{_e(body)}</div>',
        _expand_button("outcome-text", "Show full answer" if status == "completed" else "Show full message"),
    ]
    if note:
        out.append(f'<p class="outcome-note">{_e(note)}</p>')
    if answer and status != "completed":
        out.append(f'<p id="outcome-last-message" class="outcome-note"><strong>Last message:</strong> {_e(answer)}</p>')
        out.append(_expand_button("outcome-last-message", "Show full last message"))
    if buttons:
        out.append(f'<div class="outcome-actions no-print">{"".join(buttons)}</div>')
    out.append("</section>")
    return "".join(out)


def _expand_button(target: str, label: str) -> str:
    """The full text is readable without JS; enhance only when it overflows."""
    return (
        f'<button class="btn text-toggle no-print" type="button" data-expand="{_e(target)}"'
        f' aria-controls="{_e(target)}" aria-expanded="false" hidden>{_e(label)}</button>'
    )


def _copy_button(target: str, label: str) -> str:
    """A button that copies the text of the element with id `target`. Both icons
    stay in the DOM so the copy icon can cross-fade to a check when it's done."""
    return (
        f'<button class="btn no-print" type="button" data-copy="{_e(target)}">'
        f'<span class="swap" aria-hidden="true">{_icon("copy")}{_icon("check")}</span>'
        f'<span class="btn-label" aria-live="polite">{_e(label)}</span></button>'
    )


def money_digits(value: float) -> int:
    """Decimals that keep an amount from rounding away: 2 from a dollar up, 4 below it."""
    return 2 if value >= 1 else 4


def format_money(value: Optional[float], digits: Optional[int] = None) -> str:
    """$1.23, or $0.0643 below a dollar so a cheap run doesn't show as $0.00.
    Pass `digits` to line up amounts that are shown together."""
    if value is None:
        return "-"
    return f"${value:,.{money_digits(value) if digits is None else digits}f}"


def _kpi(icon: str, label: str, value: str, rows: list[tuple[str, str, str]], note: str = "", cls: str = "") -> str:
    """One figure with its breakdown always visible. `note` is HTML."""
    items = "".join(
        f'<li class="{c}"><span>{_e(k)}</span><span class="kpi-num">{_e(v)}</span></li>' for k, v, c in rows
    )
    return (
        f'<dl class="kpi {cls}"><dt>{_icon(icon)}{_e(label)}</dt><dd class="kpi-value">{_e(value)}</dd>'
        + ('<dd class="kpi-detail"><div class="kpi-breakdown">'
           + (f'<ul class="kpi-rows">{items}</ul>' if items else "")
           + (f'<p class="kpi-note">{note}</p>' if note else "")
           + '</div></dd>' if items or note else "")
        + "</dl>"
    )


def _run_cost(session: Session, views: list[dict[str, Any]], totals: dict[str, Any]) -> Optional[Cost]:
    """Each request priced on its own (long prompts can cost more per token),
    added up; the run's totals when the steps have no token counts."""
    model = str(session.run.get("model") or "")
    if not model:
        return None
    costs = [v["cost"] for v in views if v["cost"] is not None]
    if costs:
        return sum(costs, Cost())
    start = session.started_at
    return estimate_cost(model, totals.get("usage") or {}, start.date() if start else None)


def _cost_card(session: Session, cost: Optional[Cost], totals: dict[str, Any]) -> str:
    model = str(session.run.get("model") or "")
    rates = f'<a href="{_e(PRICES_URL)}" target="_blank" rel="noopener noreferrer">Standard rates</a>'
    if cost is not None:
        billed = priced_model(model) or model
        as_model = f" (billed as {_e(billed)})" if billed != model.rsplit("/", 1)[-1] else ""
        digits = money_digits(cost.total)
        rows = [
            ("Input", format_money(cost.input, digits), ""),
            ("Cached input", format_money(cost.cached, digits), ""),
            ("Output + thinking", format_money(cost.output, digits), ""),
        ]
        note = f"Paid tier, {rates} for {_e(model)}{as_model}. The free tier costs nothing."
        return _kpi("coins", "Estimated cost", format_money(cost.total, digits), rows, note, "kpi-cost")
    if not model:
        note = "The model isn't known: this session has no run.json."
    elif not (totals.get("usage") or {}).get("total_tokens"):
        note = "No token counts were logged."
    else:
        note = f"No price on file for {_e(model)}. See the {rates}."
    return _kpi("coins", "Estimated cost", "-", [], note, "kpi-cost")


def _stats(totals: dict[str, Any], cost_card: str) -> str:
    """Duration, steps, actions and tokens, each with its breakdown (the run's
    start and end are in the header)."""
    duration, model, actions = totals["duration_s"], totals["model_s"], totals["actions_s"]
    cards = []

    known = (model or 0) + (actions or 0)
    total = duration if isinstance(duration, (int, float)) and duration > 0 else known
    rows = []
    if total:
        split = [("Gemini", model), ("Actions", actions)]
        if isinstance(duration, (int, float)):
            split.append(("Other", max(0.0, total - known)))
        rows = [(name, format_duration(value), "") for name, value in split if value and value >= 0.05]
    cards.append(_kpi("clock", "Duration", format_duration(duration if duration is not None else (known or None)), rows))

    steps = totals["steps"]
    rows = []
    if isinstance(steps, int) and steps and total:
        rows.append(("Time per step", format_seconds(total / steps), ""))
    tokens = (totals["usage"] or {}).get("total_tokens")
    if isinstance(steps, int) and steps and isinstance(tokens, (int, float)) and tokens:
        rows.append(("Tokens per step", format_number(round(tokens / steps)), ""))
    retries = {k: n for k, n in (totals.get("retries") or {}).items() if n}
    for kind, n in retries.items():
        rows.append((f"Retries, {kind.replace('_', ' ')}", format_number(n), "kpi-warn"))
    cards.append(_kpi("list", "Steps", format_number(steps), rows))

    tool_calls = totals["tool_calls"]
    ranked = sorted(tool_calls.items(), key=lambda kv: -kv[1])
    rows = [(str(name), format_number(n), "mono-key") for name, n in ranked[:3]]
    if len(ranked) > 3:
        rows.append(("Other", format_number(sum(n for _, n in ranked[3:])), ""))
    if totals["tool_errors"]:
        rows.append(("Failed", format_number(totals["tool_errors"]), "kpi-bad"))
    cards.append(_kpi("pointer", "Actions", format_number(sum(tool_calls.values())), rows))

    usage = totals["usage"]
    rows = [
        (label, format_number(usage[key]), "")
        for key, label in (
            ("total_input_tokens", "Input"),
            ("total_cached_tokens", "Cached input"),
            ("total_output_tokens", "Output"),
            ("total_thought_tokens", "Thinking"),
        )
        if isinstance(usage.get(key), (int, float)) and usage[key]
    ]
    cards.append(_kpi("hash", "Tokens", format_number(usage.get("total_tokens")), rows))
    return f'<section class="kpis" aria-label="Run summary">{"".join(cards)}{cost_card}</section>'


def _screen_history(views: list[dict[str, Any]], session: Session) -> str:
    shots = [(v, a) for v in views for a in v["actions"] if a["screenshot"]]
    if not shots:
        return ""
    view, last = shots[-1]
    caption = f'Step {view["number"]} · {last["title"]}'
    href = _e(urllib.parse.quote(last["screenshot"]))
    options = []
    for i, (v, action) in enumerate(shots):
        size = action["size"]
        dims = f' data-width="{size[0]}" data-height="{size[1]}"' if size else ""
        selected = " selected" if i == len(shots) - 1 else ""
        options.append(
            f'<option value="{_e(urllib.parse.quote(action["screenshot"]))}"'
            f' data-caption="Step {_e(v["number"])} · {_e(action["title"])}"'
            f' data-step="{_e(v["number"])}"{dims}{selected}>'
            f'Capture {i + 1} · Step {_e(v["number"])} · {_e(one_line(action["title"], 80))}</option>'
        )
    return (
        '<section class="screen-history no-print" aria-labelledby="shots-title">'
        '<div class="section-head"><h2 id="shots-title">Screen history</h2>'
        f'<span class="capture-count">{len(shots)} captures</span></div>'
        '<div class="capture-toolbar" hidden><select id="capture-select" aria-label="Choose capture">'
        + ''.join(options) + '</select><span class="preview-controls">'
        f'<button class="btn btn-icon preview-prev" type="button" aria-label="Previous capture">{_icon("left")}</button>'
        f'<span id="preview-position" aria-live="polite">{len(shots)} / {len(shots)}</span>'
        f'<button class="btn btn-icon preview-next" type="button" aria-label="Next capture">{_icon("right")}</button>'
        '</span></div>'
        '<div class="preview-frame"><div class="preview-bar">'
        f'<span class="preview-context">{_icon("monitor")}<span>Captured screen</span></span>'
        f'<a class="preview-full" href="{href}" target="_blank" rel="noopener" aria-label="Open preview full size">'
        f'Full size {_icon("external")}</a></div>'
        f'<a id="preview-shot" class="shot preview-shot" href="{href}" data-caption="{_e(caption)}"'
        f' aria-label="Open screenshot viewer: {_e(caption)}">'
        f'{_img(last["screenshot"], last["size"], "Screen after: " + last["title"], eager=True)}</a>'
        '<div class="preview-caption"><span id="preview-caption" aria-live="polite">'
        f'{_e(caption)}</span><a class="preview-step" id="preview-step" href="#step-{_e(view["number"])}">View step {_icon("forward")}</a></div></div>'
        '<p class="gallery-hint">Use the arrows or choose a capture. Click the screen for full size.</p></section>'
    )


def _nav(views: list[dict[str, Any]]) -> str:
    items = []
    for v in views:
        sub = " · ".join(x for x in (_clock(v["began"]), v["offset"], format_seconds(v["seconds"])) if x and x != "-")
        items.append(
            f'<li><a href="#step-{_e(v["number"])}" class="nav-{v["kind"]}">'
            f'<span class="num">{_e(v["number"])}</span>'
            f'<span class="nav-main"><span class="nav-title">{_e(v["title"])}</span>'
            f'<span class="nav-sub">{_e(sub)}</span></span></a></li>'
        )
    return (
        '<nav class="steps-nav no-print" aria-label="Steps"><div class="nav-head">Session timeline</div>'
        f'<ol id="nav-list">{"".join(items)}</ol></nav>'
    )


def _link(url: str) -> str:
    """A URL from the log as text, clickable only when it is a web address."""
    text = _e(url)
    if urllib.parse.urlsplit(url).scheme in ("http", "https"):
        return f'<a href="{text}" rel="noopener noreferrer" target="_blank">{text}</a>'
    return text


def _kv(rows: list[tuple[str, str, str]]) -> str:
    """(class, label, html) rows as a two-column list; a row without a label spans both."""
    out = []
    for cls, label, html in rows:
        if label:
            out.append(f'<div class="kv-row {cls}"><dt>{_e(label)}</dt><dd>{html}</dd></div>')
        else:
            out.append(f'<div class="kv-row kv-wide {cls}"><dd>{html}</dd></div>')
    return f'<dl class="kv">{"".join(out)}</dl>'


def _action_html(action: dict[str, Any], view: dict[str, Any], run_start: Optional[datetime.datetime]) -> str:
    number, duration = view["number"], action["duration"]
    asked = [("", "Action", f'<code>{_e(action["name"] or "?")}</code>')]
    asked += [("kv-code", str(key), _e(_arg_value(value))) for key, value in action["args"].items()]
    sent = []
    for kind, label, text in _result_lines(action):
        sent.append((f"line-{kind}", label, _link(text) if kind == "url" else _e(text)))
    when = " → ".join(_clock(t) for t in (action["began"], action["ended"]) if t)
    if when:
        offset = _offset(run_start, action["began"] or action["ended"])
        when += f" ({offset})" if offset else ""
        if duration is not None:
            when += f" · took {format_seconds(duration)}"
        sent.append(("line-muted", "When", _e(when)))
    shot = ""
    if action["screenshot"]:
        caption = f"Step {number} · {action['title']}"
        href = _e(urllib.parse.quote(action["screenshot"]))
        shot = (
            '<figure class="shot-figure">'
            f'<a class="shot shot-full" href="{href}" data-caption="{_e(caption)}" title="Open the screenshot">'
            f'{_img(action["screenshot"], action["size"], "Screen after: " + action["title"])}</a>'
            '<figcaption><span>Screen after this action</span>'
            f'<a class="no-print" href="{href}" target="_blank" rel="noopener">Full size {_icon("external")}</a>'
            "</figcaption></figure>"
        )
    result = action["result"]
    payload_html = (
        f'<pre class="code">{_json(result.get("payload"))}</pre>' if result else '<p class="muted">Not run.</p>'
    )
    if action["failed"]:
        chip = '<span class="chip chip-bad">Failed</span>'
    elif result is None:
        chip = '<span class="chip">Not run</span>'
    else:
        chip = f'<span class="chip">{_e(format_seconds(duration)) if duration is not None else "done"}</span>'
    # A lone action's intent is already the step's title: say what it did instead.
    title = action["label"] if len(view["actions"]) == 1 else action["title"]
    subtitle = action["label"] if action["label"] != title else ""
    return (
        f'<li class="action{" failed" if action["failed"] else ""}">'
        f'<div class="action-head"><span class="action-icon">{_icon("alert" if action["failed"] else action["icon"])}</span>'
        f'<div class="action-text"><div class="action-title">{_e(title)}</div>'
        + (f'<div class="action-sub">{_e(subtitle)}</div>' if subtitle else "")
        + f"</div>{chip}</div>"
        f'<div class="action-body{" has-shot" if shot else ""}"><div class="panels">'
        f'<section class="panel"><h4 class="panel-title">Gemini asked for</h4>{_kv(asked)}</section>'
        f'<section class="panel"><h4 class="panel-title">Sent back to Gemini</h4>{_kv(sent)}</section>'
        f"</div>{shot}</div>"
        '<details class="raw"><summary>Raw call and result (JSON)</summary><div class="cols">'
        f'<div><div class="label">Arguments</div><pre class="code">{_json(action["args"])}</pre></div>'
        f'<div><div class="label">Result</div>{payload_html}</div></div></details></li>'
    )


def _step_stats(view: dict[str, Any]) -> str:
    step = view["step"]
    usage = step.get("usage") or {}
    tiles = []
    began, ended = _clock(view["began"]), _clock(view["ended"])
    when = " → ".join(x for x in (began, ended) if x)
    if when:
        tiles.append(("Time", when + (f" ({view['offset']})" if view["offset"] else ""), ""))
    if view["seconds"]:
        split = [
            f"{name} {format_seconds(step[key])}"
            for name, key in (("Gemini", "model_s"), ("actions", "actions_s"))
            if isinstance(step.get(key), (int, float))
        ]
        tiles.append(("Duration", format_seconds(view["seconds"]), " · ".join(split)))
    if isinstance(usage.get("total_tokens"), (int, float)):
        parts = [
            f"{format_number(usage[key])} {label}"
            for key, label in (
                ("total_input_tokens", "in"),
                ("total_output_tokens", "out"),
                ("total_thought_tokens", "thinking"),
                ("total_cached_tokens", "cached"),
            )
            if isinstance(usage.get(key), (int, float)) and usage[key]
        ]
        tiles.append(("Tokens", format_number(usage["total_tokens"]), " · ".join(parts)))
    if view["cost"] is not None:
        tiles.append(("Cost", format_money(view["cost"].total), "estimated"))
    retries = step.get("retries") or {}
    empty, network = retries.get("empty_response") or 0, len(retries.get("network") or [])
    if empty or network:
        detail = ", ".join(t for t in (f"{empty} empty" if empty else "", f"{network} network" if network else "") if t)
        tiles.append(("Retries", str(empty + network), detail))
    if not tiles:
        return ""
    cells = "".join(
        f'<div class="sstat"><dt>{_e(label)}</dt><dd class="sstat-value">{_e(value)}</dd>'
        + (f'<dd class="sstat-sub">{_e(sub)}</dd>' if sub else "")
        + "</div>"
        for label, value, sub in tiles
    )
    return f'<dl class="sstats">{cells}</dl>'


def _step_html(view: dict[str, Any], run_start: Optional[datetime.datetime], total: int) -> str:
    step, number = view["step"], view["number"]
    title = "Final answer" if view["kind"] == "final" else view["title"]
    badge = '<span class="chip chip-bad">Problem</span>' if view["kind"] == "failed" else ""
    actions = view["actions"]
    out = [
        f'<article class="step step-{view["kind"]}" id="step-{_e(number)}">'
        f'<header class="step-head"><span class="num">{_e(number)}</span>'
        f'<div class="step-title"><div class="step-eyebrow">Step {_e(number)} of {total}</div>'
        f"<h3>{_e(title)}</h3></div>{badge}</header>",
        _step_stats(view),
    ]
    if view["problem"]:
        out.append(f'<div class="alert">{_icon("alert")}<span>{_e(view["problem"])}</span></div>')
    if step.get("text"):
        if view["kind"] == "final":
            out.append(f'<div class="answer">{_e(step["text"])}</div>')
        else:
            out.append(
                '<div class="said"><div class="said-label">Gemini said</div>'
                f'<div class="reasoning">{_e(step["text"])}</div></div>'
            )
    if step.get("thoughts"):
        out.append(
            '<details class="thoughts"><summary>Model’s thinking</summary>'
            f'<div class="thoughts-text">{_e(step["thoughts"])}</div></details>'
        )
    if actions:
        heading = "Action" if len(actions) == 1 else f'Actions <span class="count">{len(actions)}</span>'
        out.append(
            f'<h4 class="actions-title">{heading}</h4>'
            '<ol class="actions">' + "".join(_action_html(a, view, run_start) for a in actions) + "</ol>"
        )
    out.append("</article>")
    return "".join(out)


def _settings(session: Session) -> str:
    run = session.run
    if not run:
        return ""
    settings = run.get("settings") or {}
    api = {"interactions": "Interactions API", "generate_content": "generateContent API"}.get(
        run.get("api"), run.get("api")
    )
    if run.get("api") == "interactions" and run.get("history"):
        api = f"{api}, {run['history']}-side history"
    env = settings.get("env") or run.get("environment")
    screen = settings.get("screen_size")
    versions = run.get("versions") or {}
    injection = run.get("prompt_injection_detection")
    rows = [
        ("Model", run.get("model")),
        ("API", api),
        ("Environment", " · ".join(x for x in (ENVIRONMENTS.get(env, env), run.get("computer")) if x)),
        ("Screen", f"{screen[0]} × {screen[1]}" if isinstance(screen, list) and len(screen) == 2 else None),
        ("Thinking level", run.get("thinking_level") or "default"),
        ("Screenshot resolution", run.get("media_resolution") or "default"),
        ("Safety confirmations", run.get("safety_mode")),
        ("Prompt injection detection", {True: "on", False: "off"}.get(injection, "default")),
        ("Step limit", "none" if run.get("max_steps") == 0 else run.get("max_steps")),
        ("Excluded actions", ", ".join(run.get("excluded_predefined_functions") or []) or None),
        ("Extra functions", ", ".join(run.get("custom_functions") or []) or None),
        ("Started", _when(session.started_at)),
        ("Versions", " · ".join(f"{_LIBRARIES.get(k, k)} {v}" for k, v in versions.items() if v) or None),
    ]
    items = "".join(
        f"<div><dt>{_e(k)}</dt><dd>{_e(v)}</dd></div>" for k, v in rows if v not in (None, "")
    )
    instruction = run.get("system_instruction")
    extra = (
        f'<details class="raw"><summary>System instruction</summary><pre class="code">{_e(instruction)}</pre></details>'
        if instruction
        else ""
    )
    return (
        '<section class="card settings report-panel" id="settings" aria-labelledby="settings-title">'
        '<div class="section-head"><h2 id="settings-title">Settings</h2></div>'
        f'<dl class="facts">{items}</dl>{extra}'
        f'<details class="raw"><summary>Everything in run.json</summary><pre class="code">{_json(run)}</pre></details>'
        "</section>"
    )


def _footer(session: Session) -> str:
    files = [
        (name, label)
        for name, label in ((RUN_FILE, "run.json"), (STEPS_FILE, "trajectory.jsonl"), (RESULT_FILE, "result.json"))
        if (session.path / name).exists()
    ]
    links = "".join(f'<a href="{_e(name)}">{_icon("file")}{_e(label)}</a>' for name, label in files)
    links += f'<a href=".">{_icon("folder")}Session folder</a>'
    generated = datetime.datetime.now().astimezone()
    return (
        f'<footer class="footer"><div class="files no-print">{links}</div>'
        f"<p>Made {_e(_when(generated))} from the session log. Rebuild it with "
        f"<code>python runs.py {_e(session.id)} --html</code></p></footer>"
    )


_VIEWER = (
    '<dialog id="viewer" class="viewer" aria-label="Screenshot viewer"><div class="viewer-inner">'
    '<div class="viewer-bar"><span class="viewer-count"></span><span class="viewer-caption"></span>'
    '<span class="viewer-tools"><a class="viewer-btn viewer-full" target="_blank" rel="noopener" aria-label="Open full size" title="Open full size">'
    + _icon("external")
    + '</a><button class="viewer-btn close" type="button" aria-label="Close screenshot viewer" title="Close (Esc)">'
    + _icon("x")
    + '</button></span></div><div class="viewer-stage">'
    '<button class="viewer-btn prev" type="button" aria-label="Previous screenshot" title="Previous (←)">'
    + _icon("left")
    + '</button><img alt=""><button class="viewer-btn next" type="button" aria-label="Next screenshot" title="Next (→)">'
    + _icon("right")
    + "</button></div></div></dialog>"
)


def render_report(session: Session) -> str:
    steps = session.steps()
    totals = session.totals(steps)
    tz = _tz(session)
    start, end = _run_times(session, steps, tz)
    views = _step_views(steps, session, tz)
    status = session.status
    label, tone, icon = _status(status)
    query = (session.query or "").strip()
    first_line = query.splitlines()[0].strip() if query else ""
    title = one_line(first_line, 160) or f"Session {session.id}"
    run = session.run
    env = (run.get("settings") or {}).get("env") or run.get("environment")
    when = _when(start) if start else None
    if start and end:
        when += f" → {_clock(end)}" + (f" on {end:%b} {end.day}" if end.date() != start.date() else "")
    facts = [
        ("clock", when),
        ("cpu", run.get("model")),
        ("monitor" if env == "desktop" else "globe", ENVIRONMENTS.get(env, env)),
    ]
    cost = _run_cost(session, views, totals)
    gallery = _screen_history(views, session)
    nonce = secrets.token_urlsafe(16)
    csp = (
        "default-src 'none'; img-src 'self' file: data:; style-src 'unsafe-inline'; "
        f"script-src 'nonce-{nonce}'; base-uri 'none'; form-action 'none'"
    )

    out = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f'<meta http-equiv="Content-Security-Policy" content="{csp}">'
        f"<title>{_e(label)} · {_e(one_line(first_line, 70) or session.id)}</title>"
        f"<style>{_CSS}</style></head><body>",
        '<a class="skip-link" href="#overview">Skip to report</a>',
        '<header class="masthead"><div class="wrap masthead-inner">'
        f'<a class="brand" href="#overview"><span class="brand-mark">{_icon("monitor")}</span>'
        '<span>Gemini <strong>Computer Use</strong></span></a><span class="masthead-label">Session report</span>'
        f'<span class="session-id mono">{_e(session.id)}</span>'
        f'<button class="btn theme-toggle no-print" id="theme-toggle" type="button" aria-label="Dark theme" aria-pressed="false" title="Switch color theme">{_icon("sun")}</button></div></header>',
        '<main class="wrap" id="report"><header class="report-heading">',
        '<p class="overline">The task</p>',
        f"<h1>{_e(title)}</h1>",
        '<ul class="facts-line">'
        + "".join(f"<li>{_icon(i)}<span>{_e(text)}</span></li>" for i, text in facts if text)
        + "</ul>",
        '</header>',
        '<nav class="report-nav no-print" aria-label="Report sections"><div class="section-links">'
        '<a href="#overview" id="tab-overview" aria-controls="overview">Overview</a>'
        '<a href="#activity" id="tab-activity" aria-controls="activity">Activity</a>'
        + ('<a href="#settings" id="tab-settings" aria-controls="settings">Settings</a>' if session.run else '')
        + '</div><span class="report-nav-note">Saved session · Local report</span></nav>',
        '<section class="report-panel" id="overview" aria-label="Overview">',
        f'<div class="summary{" no-captures" if not gallery else ""}">{_outcome(session, views)}{gallery}'
        f'{_stats(totals, _cost_card(session, cost, totals))}</div>',
    ]
    if (query and query != first_line) or len(first_line) > 160:
        out.append(
            '<section class="card task" aria-labelledby="task-title"><div class="section-head">'
            f'<h2 id="task-title">Task</h2>{_copy_button("task-text", "Copy task")}</div>'
            f'<div id="task-text" class="task-text">{_e(query)}</div>{_expand_button("task-text", "Show full task")}</section>'
        )
    out.append('</section>')
    if views:
        out.append(
            '<section class="activity report-panel" id="activity" aria-labelledby="activity-title">'
            '<header class="activity-heading"><div><p class="overline">Execution log</p>'
            f'<h2 id="activity-title">Step by step <span class="count">{len(views)}</span></h2></div>'
            '<span class="hint no-print">Browse the timeline or use the ← → keys</span></header>'
            f'<div class="layout">{_nav(views)}<section class="steps" aria-labelledby="steps-title">'
            '<div class="section-head"><h2 id="steps-title">Step details</h2>'
            '<div class="steps-tools no-print">'
            '<div class="pager" id="pager" hidden><button class="btn btn-icon" type="button" id="step-prev"'
            f' aria-label="Previous step" title="Previous step (←)">{_icon("left")}</button>'
            '<span class="pager-count" id="pager-count" aria-live="polite"></span>'
            '<button class="btn btn-icon" type="button" id="step-next"'
            f' aria-label="Next step" title="Next step (→)">{_icon("right")}</button></div>'
            '<button class="btn" type="button" id="toggle-details">Expand all</button></div></div>'
            + "".join(_step_html(v, start, len(views)) for v in views)
            + "</section></div></section>"
        )
    else:
        out.append(
            '<section class="card empty report-panel" id="activity" aria-label="Activity"><div class="section-head"><h2>Step by step</h2></div>'
            '<p class="muted">No steps were logged: the run ended before Gemini answered the first request.</p></section>'
        )
    out.append(_settings(session))
    out.append(_footer(session))
    out.append(f"</main>{_VIEWER}<script nonce=\"{nonce}\">{_JS}</script></body></html>")
    return "".join(out)


def write_report(session: Session) -> pathlib.Path:
    path = session.path / REPORT_FILE
    path.write_text(render_report(session), encoding="utf-8")
    return path


def _in_container() -> bool:
    return os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv")


def report_link(path: "str | pathlib.Path", label: str = "Report: ") -> Text:
    """'Report: file:///...report.html' for the terminal, as a link you can
    click. Inside a container that URL would point into the container, so it's
    the report's place in the project's runs/ folder (mounted from your computer)
    instead."""
    path = pathlib.Path(path).resolve()
    if _in_container():
        try:
            inside = path.relative_to(RUNS_DIR)
        except ValueError:
            pass
        else:
            return Text(f"{label}runs/{inside.as_posix()}  (in the project folder on your computer)")
    url = path.as_uri()
    return Text.assemble(label, (url, Style(link=url)))


def open_in_browser(path: "str | pathlib.Path") -> bool:
    """Opens a report in the default web browser. Clicking its link isn't
    always enough: VS Code and Cursor open file:// links from their terminal
    in the editor. False when no browser could be opened (in a container the
    browser is on the host, so it isn't tried)."""
    if _in_container():
        return False
    try:
        return webbrowser.open(pathlib.Path(path).resolve().as_uri())
    except webbrowser.Error:
        return False


# ---------------------------------------------------------------------------
# style and script
# ---------------------------------------------------------------------------

_CSS = """
:root {
  color-scheme: light;
  /* Generated in OKLCH with culori and emitted as sRGB hex. Components use roles. */
  --neutral-0: #fdfdfd; --neutral-50: #fafafa; --neutral-100: #eeeeee;
  --neutral-200: #e1e1e1; --neutral-300: #d4d4d4; --neutral-400: #bababa;
  --neutral-500: #8f8f8f; --neutral-600: #717171; --neutral-650: #636363;
  --neutral-700: #505050; --neutral-750: #3d3d3d; --neutral-800: #303030;
  --neutral-850: #262626; --neutral-900: #1d1d1d; --neutral-950: #121212;
  --teal-50: #e1f3f3; --teal-300: #9ad0d2; --teal-700: #2d585a; --teal-900: #242a2a;
  --green-50: #e3f4e6; --green-300: #9ad5a5; --green-700: #2d5c39; --green-900: #242b25;
  --red-50: #f3edec; --red-300: #ddbbb7; --red-700: #83312e; --red-900: #322524;
  --amber-50: #f3eee3; --amber-300: #d8c192; --amber-700: #5e4d29; --amber-900: #2b2923;
  --bg: var(--neutral-50); --surface: var(--neutral-0); --sunken: var(--neutral-100); --hover: var(--neutral-200);
  --text: var(--neutral-900); --muted: var(--neutral-700); --faint: var(--neutral-650);
  --line: var(--neutral-300); --line-strong: var(--neutral-500);
  --accent: var(--teal-700); --accent-soft: var(--teal-50);
  --ok: var(--green-700); --ok-soft: var(--green-50);
  --bad: var(--red-700); --bad-soft: var(--red-50);
  --warn: var(--amber-700); --warn-soft: var(--amber-50);
  --idle: var(--neutral-700); --idle-soft: var(--neutral-100);
  --inverse: var(--neutral-0); --brand-solid: var(--neutral-900); --brand-text: var(--neutral-0);
  --focus: var(--teal-700); --shot-outline: var(--neutral-500);
  --scrim: var(--neutral-950); --overlay-surface: var(--neutral-850);
  --overlay-hover: var(--neutral-750); --overlay-muted: var(--neutral-400);
  --image-surface: var(--neutral-0); --shadow-color: var(--neutral-900);
  --shadow: 0 3px 14px color-mix(in srgb, var(--shadow-color) 4%, transparent);
  --mono: ui-monospace, "Cascadia Code", "SF Mono", Menlo, Consolas, monospace;
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --bg: var(--neutral-950); --surface: var(--neutral-900); --sunken: var(--neutral-850); --hover: var(--neutral-800);
  --text: var(--neutral-100); --muted: var(--neutral-300); --faint: var(--neutral-400);
  --line: var(--neutral-750); --line-strong: var(--neutral-600);
  --accent: var(--teal-300); --accent-soft: var(--teal-900);
  --ok: var(--green-300); --ok-soft: var(--green-900);
  --bad: var(--red-300); --bad-soft: var(--red-900);
  --warn: var(--amber-300); --warn-soft: var(--amber-900);
  --idle: var(--neutral-300); --idle-soft: var(--neutral-850);
  --brand-solid: var(--neutral-100); --brand-text: var(--neutral-900);
  --focus: var(--teal-300); --shot-outline: var(--neutral-600); --shadow-color: var(--neutral-950);
  --shadow: none;
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; scroll-padding-top: 24px; }
@media (prefers-reduced-motion: reduce) { html { scroll-behavior: auto; } }
body { margin: 0; background: var(--bg); color: var(--text);
  font: 14px/1.6 "Segoe UI", "Helvetica Neue", sans-serif;
  -webkit-font-smoothing: antialiased; }
a { color: var(--accent); }
code, .mono { font-family: var(--mono); font-size: .9em; }
.wrap { max-width: 1440px; margin: 0 auto; padding: 0 48px; }
.i { width: 16px; height: 16px; flex: none; }
.muted { color: var(--muted); }
.bad-text { color: var(--bad); font-weight: 600; }
.tone-ok { --tone: var(--ok); --tone-soft: var(--ok-soft); }
.tone-bad { --tone: var(--bad); --tone-soft: var(--bad-soft); }
.tone-warn { --tone: var(--warn); --tone-soft: var(--warn-soft); }
.tone-idle { --tone: var(--idle); --tone-soft: var(--idle-soft); }

/* A quiet document header above the review workspace. */
.skip-link { position: absolute; z-index: 5; top: 8px; left: 16px; padding: 8px 14px;
  transform: translateY(-150%); background: var(--surface); }
.skip-link:focus { transform: none; }
.masthead { background: var(--surface); border-bottom: 1px solid var(--line); }
.masthead-inner { display: flex; align-items: center; gap: 24px; min-height: 60px; }
.brand { display: flex; align-items: center; gap: 12px; color: var(--text); text-decoration: none; font-size: 14px; }
.brand strong { font-weight: 650; }
.brand-mark { display: grid; place-items: center; width: 32px; height: 32px; background: var(--brand-solid); color: var(--brand-text); border-radius: 8px; }
.brand-mark .i { width: 18px; height: 18px; stroke-width: 1.6; }
.masthead-label { padding-left: 24px; border-left: 1px solid var(--line); color: var(--muted); font-size: 13px; }
.session-id { margin-left: auto; color: var(--muted); font-size: 11px; letter-spacing: .02em; }
.theme-toggle { display: inline-grid; place-items: center; flex: none; width: 34px; height: 34px; padding: 0; }
.overline { margin: 0; color: var(--muted); font: 500 10px/1.5 var(--mono); letter-spacing: .14em; text-transform: uppercase; }
.report-heading { padding: 26px 0 22px; }
h1 { max-width: 1000px; margin: 8px 0 16px; font: 400 34px/1.22 Georgia, "Times New Roman", serif;
  letter-spacing: -.035em; text-wrap: balance; overflow-wrap: anywhere; }
.badge { display: inline-flex; align-items: center; gap: 6px; padding: 3px 11px 3px 8px; border-radius: 999px;
  background: var(--tone-soft); color: var(--tone); font-size: 13px; font-weight: 600; line-height: 1.5; }
.badge .i { width: 14px; height: 14px; stroke-width: 2.5; }
.facts-line { display: flex; flex-wrap: wrap; gap: 8px 24px; margin: 0; padding: 0; list-style: none;
  color: var(--muted); font-size: 12px; font-variant-numeric: tabular-nums; }
.facts-line li { display: inline-flex; align-items: center; gap: 7px; min-width: 0; }
.facts-line .i { width: 15px; height: 15px; color: var(--faint); stroke-width: 1.75; }
.facts-line span { overflow-wrap: anywhere; }
main.wrap { padding-bottom: 56px; }
.report-nav { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--line); }
.section-links { display: flex; gap: 28px; }
.section-links a { display: block; padding: 12px 2px; color: var(--muted); border-bottom: 2px solid transparent;
  text-decoration: none; font-size: 13px; font-weight: 600; }
.section-links a[aria-selected="true"] { color: var(--accent); border-bottom-color: var(--accent); }
.section-links a:hover { color: var(--accent); }
.report-nav-note { font-size: 11px; color: var(--faint); }
.report-panel[hidden] { display: none; }
.report-panel:focus-visible { outline-offset: 8px; }

/* shared */
h2 { margin: 0; font-size: 16px; font-weight: 650; }
.section-head { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 16px; }
.count { display: inline-block; min-width: 22px; margin-left: 4px; padding: 0 7px; border-radius: 999px;
  background: var(--sunken); color: var(--muted); font-size: 12.5px; font-weight: 600; text-align: center; }
.hint { color: var(--faint); font-size: 12px; }
.card { background: var(--surface); border: 1px solid var(--line); border-radius: 6px; padding: 24px;
  box-shadow: var(--shadow); margin-top: 16px; }
.btn { position: relative; display: inline-flex; align-items: center; justify-content: center; gap: 7px; padding: 7px 12px;
  border: 1px solid var(--line-strong); border-radius: 5px; background: var(--surface); color: var(--text);
  font: 500 12px/1.4 "Segoe UI", sans-serif; text-decoration: none; white-space: nowrap; cursor: pointer;
  transition-property: background-color, scale; transition-duration: .15s; transition-timing-function: ease-out; }
@media (hover: hover) { .btn:not(:disabled):hover { background: var(--hover); } }
.btn:active { scale: .96; }
.btn::after { content: ""; position: absolute; inset: -6px -3px; }  /* a 40px tall hit area */
.btn .i { width: 14px; height: 14px; stroke-width: 1.75; }  /* lighter beside 500-weight text */
.btn > .swap:first-child { margin-left: -2px; }  /* icons read as wider than they are */
.swap { display: inline-grid; flex: none; }
.swap .i { grid-area: 1 / 1; transition-property: opacity, scale, filter; transition-duration: .2s;
  transition-timing-function: cubic-bezier(.2, 0, 0, 1); }
.swap .i + .i, .copied .swap .i:first-child { opacity: 0; scale: .25; filter: blur(4px); }
.copied .swap .i + .i { opacity: 1; scale: 1; filter: blur(0); color: var(--ok); }
@media (prefers-reduced-motion: reduce) { .btn, .swap .i { transition-duration: 0s; } }
:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }
.code { margin: 0; padding: 10px 12px; border-radius: 8px; background: var(--sunken); overflow: auto; max-height: 420px;
  white-space: pre-wrap; overflow-wrap: anywhere; font: 12.5px/1.5 var(--mono); }
.label { margin-bottom: 4px; color: var(--muted); font-size: 12.5px; }
details > summary { cursor: pointer; color: var(--muted); font-size: 12px; width: fit-content; list-style: none;
  display: flex; align-items: center; gap: 6px; }
details > summary::-webkit-details-marker { display: none; }
details > summary::before { content: ""; width: 6px; height: 6px; border: solid currentColor; border-width: 0 1.5px 1.5px 0;
  transform: rotate(-45deg); transition: transform .15s; }
details[open] > summary::before { transform: rotate(45deg); }
details > summary:hover { color: var(--text); }

/* Balance the result with a readable preview and a grid of captures. */
.summary { display: grid; grid-template-columns: minmax(0, 1fr) min(46%, 560px); grid-template-rows: min-content 1fr;
  column-gap: 40px; align-items: start; margin-top: 32px; }
.summary.no-captures { grid-template-columns: minmax(0, 1fr); }
.outcome { grid-column: 1; grid-row: 1; min-width: 0; padding: 0 0 28px; }
.no-captures .outcome { grid-column: 1; }
.summary.no-captures .outcome { max-width: 760px; }
.outcome-top { display: flex; flex-wrap: wrap; align-items: center; gap: 10px 16px; }
.outcome-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 24px; }
.outcome .badge { padding: 3px 8px; font-size: 11px; }
.outcome h2 { margin-top: 25px; font-size: 24px; line-height: 1.3; font-weight: 600; letter-spacing: -.025em; text-wrap: balance; }
.outcome-prose { margin-top: 14px; max-width: 65ch; white-space: pre-wrap; overflow-wrap: anywhere; text-wrap: pretty;
  color: var(--muted); font-size: 14px; line-height: 1.85; }
.outcome-mono { margin-top: 10px; padding: 10px 12px; border-radius: 8px; white-space: pre-wrap; overflow-wrap: anywhere;
  background: var(--surface); border: 1px solid color-mix(in srgb, var(--tone) 28%, transparent);
  font: 13px/1.55 var(--mono); }
.outcome-note { margin: 10px 0 0; max-width: 72ch; color: var(--muted); white-space: pre-wrap; text-wrap: pretty; }
.text-toggle { margin-top: 10px; }
.text-toggle[hidden] { display: none; }
.is-collapsed { max-block-size: calc(8 * 1lh); overflow: hidden; }

/* Figures and their breakdowns stay together and remain visible. */
.kpis { grid-column: 1; grid-row: 2; min-width: 0; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 28px 32px; padding: 24px 0 0; border-top: 1px solid var(--line); }
.kpi { min-width: 0; margin: 0; display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 0 12px; align-content: start; }
.no-captures .kpis { max-width: 960px; }
.kpi dt { display: flex; align-items: center; gap: 7px; min-width: 0; align-self: center; color: var(--muted); font-size: 12px; font-weight: 600; }
.kpi dt .i { width: 13px; height: 13px; color: var(--faint); stroke-width: 1.6; }
.kpi dd { margin: 0; }
.kpi .kpi-value { font-size: 24px; line-height: 1.2; font-weight: 600; letter-spacing: -.04em;
  font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.kpi-detail { grid-column: 1 / -1; margin-top: 12px !important; }
.kpi-rows { display: grid; gap: 7px; margin: 0; padding: 0;
  list-style: none; font-size: 12px; }
.kpi-rows li { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; color: var(--muted); }
.kpi-rows li > span:first-child { min-width: 0; overflow-wrap: anywhere; }
.kpi-num { color: var(--text); font-weight: 500; font-variant-numeric: tabular-nums; white-space: nowrap; }
.kpi-rows .mono-key > span:first-child { font-family: var(--mono); font-size: 12.5px; }
.kpi-rows .kpi-bad, .kpi-rows .kpi-bad .kpi-num { color: var(--bad); }
.kpi-rows .kpi-warn, .kpi-rows .kpi-warn .kpi-num { color: var(--warn); }
.kpi .kpi-note { margin: 12px 0 0; color: var(--muted); font-size: 11px; line-height: 1.6; text-wrap: pretty; overflow-wrap: anywhere; }
.kpi-cost { grid-column: 1 / -1; padding-top: 22px; border-top: 1px solid var(--line); }
.kpi-cost .kpi-rows { grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; }
.kpi-cost .kpi-rows li { flex-direction: column; gap: 3px; }
.kpi-note a { color: var(--muted); }
@media (hover: hover) { .kpi-note a:hover { color: var(--accent); } }
@media (min-width: 641px) and (max-width: 1100px) {
  .summary { column-gap: 32px; }
}

/* task */
.task .section-head { margin-bottom: 10px; }
.task-text { white-space: pre-wrap; overflow-wrap: anywhere; text-wrap: pretty; line-height: 1.6; }

/* One capture inspector: the page height is independent of capture count. */
.screen-history { grid-column: 2; grid-row: 1 / span 2; min-width: 0; position: sticky; top: 24px; align-self: start; }
.screen-history .section-head { margin-bottom: 15px; }
.screen-history h2 { font-size: 13px; font-weight: 600; }
.capture-count { font: 10px var(--mono); color: var(--muted); }
.capture-toolbar { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; }
.capture-toolbar[hidden] { display: none; }
#capture-select { min-width: 0; flex: 1; width: 100%; min-height: 36px; padding: 6px 8px; border: 1px solid var(--line-strong);
  border-radius: 5px; background: var(--surface); color: var(--text); font: 12px "Segoe UI", sans-serif; text-overflow: ellipsis; }
.preview-frame { overflow: hidden; border: 1px solid var(--line-strong); border-radius: 7px; background: var(--surface); box-shadow: var(--shadow); }
.preview-bar { display: flex; align-items: center; justify-content: space-between; min-height: 34px; padding: 0 12px;
  border-bottom: 1px solid var(--line); background: var(--sunken); font-size: 10px; color: var(--muted); }
.preview-context, .preview-full { display: inline-flex; align-items: center; gap: 7px; }
.preview-context .i, .preview-full .i { width: 12px; height: 12px; stroke-width: 1.5; }
.preview-full { color: var(--muted); text-decoration: none; }
.preview-full:hover { color: var(--accent); }
.preview-shot { display: block; background: var(--sunken); cursor: zoom-in; }
.preview-shot img { display: block; width: 100%; height: auto; aspect-ratio: 16 / 10; object-fit: contain; object-position: center; }
.preview-caption { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; padding: 12px;
  border-top: 1px solid var(--line); font-size: 10px; color: var(--muted); }
#preview-caption { min-width: 0; max-block-size: 4.8em; overflow: auto; overflow-wrap: anywhere; line-height: 1.6; }
.preview-step { display: inline-flex; align-items: center; flex: none; gap: 5px; text-decoration: none; font-size: 11px; }
.preview-step .i { width: 13px; height: 13px; }
.preview-controls { display: inline-flex; flex: none; align-items: center; gap: 6px; }
.preview-controls .btn { width: 32px; min-height: 36px; padding: 0; }
[dir="rtl"] .preview-controls .i, [dir="rtl"] .preview-step .i { transform: scaleX(-1); }
#preview-position { min-width: 42px; font-family: var(--mono); text-align: center; }
.gallery-hint { margin: 4px 0 0; color: var(--faint); font-size: 10px; }
@media (max-width: 900px) {
  .summary { grid-template-columns: minmax(0, 1fr); gap: 28px; }
  .outcome { grid-column: 1; grid-row: 1; padding-bottom: 0; }
  .screen-history { grid-column: 1; grid-row: 2; width: 100%; max-width: 560px; position: static; }
  .kpis { grid-column: 1; grid-row: 3; }
  .no-captures .kpis { grid-row: 2; }
}
@media (max-height: 700px) { .screen-history { position: static; } }

/* A timeline beside the selected step. */
.activity { margin-top: 32px; }
.activity-heading { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: end; gap: 12px; margin-bottom: 24px; }
.activity-heading h2 { margin-top: 6px; font: 400 28px/1.2 Georgia, "Times New Roman", serif; letter-spacing: -.025em; }
.activity-heading .count { vertical-align: middle; font-family: "Segoe UI", sans-serif; }
.layout { display: grid; grid-template-columns: minmax(0, 1fr); gap: 28px; }
@media (min-width: 1000px) { .layout { grid-template-columns: 264px minmax(0, 1fr); } }
.steps-nav { display: none; }
@media (min-width: 1000px) {
  .steps-nav { display: block; position: sticky; top: 16px; align-self: start; max-height: calc(100vh - 32px);
    background: transparent; border-right: 1px solid var(--line); padding-right: 20px;
    overflow: hidden; }
}
.nav-head { padding: 0 8px 16px; font-size: 11px; font-weight: 600; color: var(--muted); }
.steps-nav ol { position: relative; margin: 0; padding: 0 6px 8px; list-style: none; overflow-y: auto;
  max-height: calc(100vh - 80px); }
.steps-nav a { display: grid; grid-template-columns: 24px minmax(0, 1fr); gap: 10px; align-items: start;
  padding: 10px 8px; border-radius: 5px; color: var(--text); text-decoration: none; }
.steps-nav a:hover { background: var(--hover); }
.steps-nav a.current { background: var(--accent-soft); color: var(--accent); }
.num { display: inline-grid; place-items: center; width: 24px; height: 24px; border-radius: 50%;
  background: var(--sunken); color: var(--muted); font-size: 12px; font-weight: 650; font-variant-numeric: tabular-nums; }
.nav-failed .num, .step-failed .step-head .num { background: var(--bad-soft); color: var(--bad); }
.nav-final .num, .step-final .step-head .num { background: var(--ok-soft); color: var(--ok); }
.nav-main { min-width: 0; }
.nav-title { display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; overflow: hidden;
  font-size: 12px; line-height: 1.5; overflow-wrap: anywhere; }
.nav-sub { display: block; margin-top: 2px; color: var(--faint); font-size: 12px; font-variant-numeric: tabular-nums; }
.step { background: var(--surface); border: 1px solid var(--line); border-radius: 6px; padding: 24px;
  box-shadow: var(--shadow); margin-bottom: 14px; scroll-margin-top: 16px; }
.steps.single .step { display: none; margin-bottom: 0; }
.steps.single .step.active { display: block; }
.steps-tools { display: flex; align-items: center; gap: 10px; }
.pager { display: flex; align-items: center; gap: 6px; }
.pager[hidden] { display: none; }
.pager-count { min-width: 56px; color: var(--muted); font-size: 13px; text-align: center; font-variant-numeric: tabular-nums; }
.btn-icon { padding: 5px 7px; }
.btn:disabled { opacity: .45; cursor: default; }
.btn:disabled:active { scale: 1; }
.step-failed { border-color: color-mix(in srgb, var(--bad) 35%, var(--line)); }
.step-final { border-color: color-mix(in srgb, var(--ok) 35%, var(--line)); }
.step-head { display: flex; align-items: center; gap: 12px; }
.step-head .num { flex: none; width: 34px; height: 34px; font-size: 13.5px; }
.step-title { flex: 1; min-width: 0; }
.step-eyebrow { color: var(--muted); font-size: 12.5px; font-weight: 500; font-variant-numeric: tabular-nums; }
.step-head h3 { margin: 1px 0 0; font-size: 18px; line-height: 1.3; font-weight: 650; letter-spacing: -.01em;
  overflow-wrap: anywhere; text-wrap: balance; }

/* the step's figures: one row, divided like the run totals */
.sstats { display: grid; grid-template-columns: repeat(auto-fit, minmax(145px, 1fr)); margin: 20px 0 0;
  border: 1px solid var(--line); border-radius: 4px; overflow: hidden; }
.sstat { min-width: 0; padding: 10px 14px 11px; box-shadow: -1px 0 0 var(--line), 0 -1px 0 var(--line); }
.sstat dt { color: var(--muted); font-size: 12px; font-weight: 500; }
.sstat-value { margin: 2px 0 0; font-size: 13px; font-weight: 600; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.sstat-sub { margin: 2px 0 0; color: var(--faint); font-size: 12px; font-variant-numeric: tabular-nums; }

.said { margin-top: 16px; }
.said-label, .actions-title { margin: 0; color: var(--muted); font-size: 12.5px; font-weight: 600; }
.reasoning { margin-top: 4px; white-space: pre-wrap; overflow-wrap: anywhere; }
.answer { margin-top: 16px; padding: 12px 14px; border-radius: 8px; background: var(--ok-soft);
  white-space: pre-wrap; overflow-wrap: anywhere; }
.alert { display: flex; gap: 8px; align-items: flex-start; margin-top: 16px; padding: 10px 12px; border-radius: 8px;
  background: var(--bad-soft); color: var(--bad); font-size: 14px; overflow-wrap: anywhere; }
.alert .i { margin-top: 2px; }
.thoughts { margin-top: 12px; }
.thoughts-text { margin-top: 8px; padding: 10px 12px; border-radius: 8px; background: var(--sunken);
  color: var(--muted); white-space: pre-wrap; overflow-wrap: anywhere; font-size: 14px; }

/* actions */
.actions-title { margin-top: 22px; padding-top: 18px; border-top: 1px solid var(--line); }
.actions { margin: 10px 0 0; padding: 0; list-style: none; }
.action + .action { margin-top: 22px; padding-top: 22px; border-top: 1px dashed var(--line); }
.action-head { display: grid; grid-template-columns: 32px minmax(0, 1fr) auto; gap: 12px; align-items: center; }
.action-icon { display: grid; place-items: center; width: 32px; height: 32px; border-radius: 8px;
  background: var(--sunken); color: var(--muted); }
.action-icon .i { stroke-width: 2; }
.action.failed .action-icon { background: var(--bad-soft); color: var(--bad); }
.action-title { font-weight: 600; overflow-wrap: anywhere; }
.action-sub { margin-top: 1px; color: var(--muted); font-size: 13px; overflow-wrap: anywhere; }
.chip { padding: 2px 9px; border-radius: 999px; background: var(--sunken); color: var(--muted);
  font-size: 12.5px; font-weight: 500; font-variant-numeric: tabular-nums; white-space: nowrap; }
.chip-ok { background: var(--ok-soft); color: var(--ok); }
.chip-bad { background: var(--bad-soft); color: var(--bad); }
.step-head .chip { flex: none; }

.action-body { display: grid; grid-template-columns: minmax(0, 1fr); gap: 14px; margin-top: 12px; align-items: start; }
.action-body.has-shot { grid-template-columns: minmax(0, 1fr) 280px; }
.panels { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; min-width: 0; }
.has-shot .panels { grid-template-columns: minmax(0, 1fr); }
.panel { min-width: 0; padding: 14px; border-radius: 4px; background: var(--sunken); }
.panel-title { margin: 0 0 8px; color: var(--muted); font-size: 12px; font-weight: 600; }
.kv { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 5px 14px; margin: 0; font-size: 13.5px; }
.kv-row { display: contents; }
.kv dt { color: var(--muted); }
.kv dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
.kv-wide dd { grid-column: 1 / -1; }
.kv code, .kv-code dd { font-family: var(--mono); font-size: 12.5px; }
.kv-code dd { white-space: pre-wrap; }
.line-error dt, .line-error dd { color: var(--bad); font-weight: 600; }
.line-muted dd { color: var(--muted); font-variant-numeric: tabular-nums; }
.line-url a { color: var(--accent); text-decoration: none; }
@media (hover: hover) { .line-url a:hover { text-decoration: underline; } }

.shot-figure { margin: 0; min-width: 0; }
.shot-full { display: block; position: relative; border-radius: 8px; overflow: hidden; background: var(--sunken);
  cursor: zoom-in; }
.shot-full img { display: block; width: 100%; height: auto; }
.shot-full::after { content: ""; position: absolute; inset: 0; border-radius: inherit; pointer-events: none;
  outline: 1px solid var(--shot-outline); outline-offset: -1px; transition: outline-color .15s ease-out; }
@media (hover: hover) { .shot-full:hover::after { outline: 2px solid var(--accent); outline-offset: -2px; } }
.shot-figure figcaption { display: flex; justify-content: space-between; gap: 12px; margin-top: 6px;
  color: var(--faint); font-size: 12.5px; }
.shot-figure figcaption a { display: inline-flex; align-items: center; gap: 4px; color: var(--muted); text-decoration: none;
  white-space: nowrap; }
.shot-figure figcaption .i { width: 13px; height: 13px; stroke-width: 1.75; }
@media (hover: hover) { .shot-figure figcaption a:hover { color: var(--accent); } }
.raw { margin: 12px 0 0; }
.cols { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 12px; margin-top: 10px; }
@media (max-width: 760px) {
  .cols, .panels, .action-body.has-shot { grid-template-columns: minmax(0, 1fr); }
  .shot-figure { max-width: 320px; }
}

/* settings and footer */
.settings { margin-top: 36px; }
.facts { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 12px 24px; margin: 0 0 14px; }
.facts dt { color: var(--muted); font-size: 12.5px; }
.facts dd { margin: 1px 0 0; overflow-wrap: anywhere; }
.settings .raw { margin: 10px 0 0; }
.footer { margin-top: 28px; color: var(--muted); font-size: 13px; }
.footer p { margin: 10px 0 0; }
.files { display: flex; flex-wrap: wrap; gap: 8px 18px; }
.files a { display: inline-flex; align-items: center; gap: 6px; text-decoration: none; }
.files a:hover { text-decoration: underline; }

/* screenshot viewer */
.viewer { width: 100vw; height: 100vh; max-width: none; max-height: none; margin: 0; padding: 0; inset: 0;
  border: 0; background: var(--scrim); color: var(--inverse); }
.viewer::backdrop { background: var(--scrim); }
.viewer-inner { display: grid; grid-template-rows: auto minmax(0, 1fr); height: 100%; }
.viewer-bar { display: flex; align-items: center; gap: 14px; padding: 12px 16px; font-size: 14px; }
.viewer-count { color: var(--overlay-muted); font-variant-numeric: tabular-nums; white-space: nowrap; }
.viewer-caption { flex: 1; min-width: 0; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.viewer-tools { display: flex; gap: 8px; }
.viewer-stage { display: grid; grid-template-columns: 56px minmax(0, 1fr) 56px; align-items: center; min-height: 0; padding-bottom: 16px; }
.viewer-stage img { display: block; max-width: 100%; max-height: calc(100vh - 90px); margin: auto; border-radius: 6px;
  box-shadow: 0 10px 40px color-mix(in srgb, var(--shadow-color) 50%, transparent); background: var(--image-surface); }
.viewer-btn { display: inline-grid; place-items: center; width: 40px; height: 40px; margin: auto; border: 0; border-radius: 50%;
  background: var(--overlay-surface); color: var(--inverse); cursor: pointer; }
.viewer-btn:hover { background: var(--overlay-hover); }
.viewer-btn .i { width: 20px; height: 20px; }

@media (max-width: 640px) {
  .wrap { padding-left: 20px; padding-right: 20px; }
  .masthead-inner { min-height: 60px; gap: 12px; }
  .masthead-label, .session-id, .report-nav-note { display: none; }
  .theme-toggle { margin-left: auto; }
  .report-heading { padding: 26px 0 20px; }
  h1 { font-size: 29px; margin-bottom: 16px; }
  .facts-line { font-size: 11px; gap: 8px 16px; }
  .facts-line li:first-child { flex-basis: 100%; }
  .kpis, .no-captures .kpis { grid-column: 1; grid-template-columns: repeat(2, minmax(0, 1fr)); grid-row: 3; gap: 24px 20px; padding: 20px 0; border-bottom: 1px solid var(--line); }
  .kpi .kpi-value { font-size: 21px; }
  .kpi dt { font-size: 11px; gap: 5px; }
  .kpi { display: block; }
  .kpi .kpi-value { margin-top: 6px; }
  .kpi-cost .kpi-rows { gap: 10px; }
  .summary { grid-template-columns: minmax(0, 1fr); gap: 28px; margin-top: 24px; }
  .no-captures .kpis { grid-row: 2; }
  .outcome { grid-column: 1; grid-row: 1; padding: 0; }
  .outcome h2 { margin-top: 14px; font-size: 23px; }
  .outcome-prose { margin-top: 10px; font-size: 13px; }
  .outcome-actions { margin-top: 16px; }
  .card, .step { padding: 18px; }
  .activity { margin-top: 26px; }
  .activity-heading .hint { display: none; }
  .steps-tools { gap: 6px; }
  .steps > .section-head { align-items: flex-start; }
  .steps > .section-head h2 { flex-basis: 100%; font-size: 13px; }
  .steps-tools { width: 100%; justify-content: space-between; }
  .step-head h3 { font-size: 16px; }
  .kv { grid-template-columns: minmax(60px, max-content) minmax(0, 1fr); gap: 5px 10px; font-size: 12px; }
  .kpi-rows { font-size: 12px; }
  .btn { min-height: 36px; }
  .viewer-stage { grid-template-columns: 40px minmax(0, 1fr) 40px; }
}
@media (min-width: 641px) and (max-width: 1100px) {
  .wrap { padding-left: 32px; padding-right: 32px; }
  .masthead-inner { gap: 16px; }
  .masthead-label { padding-left: 16px; }
  .session-id { font-size: 10px; }
}
@media print {
  :root, :root[data-theme="dark"] { --bg: var(--neutral-0); --surface: var(--neutral-0); --sunken: var(--neutral-100);
    --text: var(--neutral-900); --muted: var(--neutral-700); --faint: var(--neutral-650);
    --line: var(--neutral-300); --line-strong: var(--neutral-500); --accent: var(--teal-700);
    --ok: var(--green-700); --ok-soft: var(--green-50); --bad: var(--red-700); --bad-soft: var(--red-50);
    --warn: var(--amber-700); --warn-soft: var(--amber-50); color-scheme: light; }
  body { background: var(--bg); }
  .no-print, .viewer { display: none !important; }
  .report-panel[hidden] { display: block; }
  .layout { display: block; }
  .summary { display: block; }
  .is-collapsed { max-block-size: none; overflow: visible; }
  .step, .card, .outcome, .kpi, .action { box-shadow: none; break-inside: avoid; }
  .steps.single .step { display: block; }
}
"""

_JS = """
(() => {
  const theme = document.getElementById('theme-toggle');
  const setTheme = (dark) => {
    document.documentElement.dataset.theme = dark ? 'dark' : 'light';
    if (theme) theme.setAttribute('aria-pressed', String(dark));
  };
  try { setTheme(localStorage.getItem('report-theme') === 'dark'); } catch (e) { setTheme(false); }
  if (theme) theme.addEventListener('click', () => {
    const dark = document.documentElement.dataset.theme !== 'dark';
    setTheme(dark);
    try { localStorage.setItem('report-theme', dark ? 'dark' : 'light'); } catch (e) { /* file: storage may be unavailable */ }
  });

  const expanders = [...document.querySelectorAll('[data-expand]')];
  const measureExpanders = () => {
    for (const button of expanders) {
      const target = document.getElementById(button.dataset.expand);
      if (!target || !target.getClientRects().length) continue;
      const expanded = button.getAttribute('aria-expanded') === 'true';
      const style = getComputedStyle(target);
      const limit = (parseFloat(style.lineHeight) || parseFloat(style.fontSize) * 1.6) * 8;
      target.classList.remove('is-collapsed');
      const overflows = target.scrollHeight > limit + 1;
      target.classList.toggle('is-collapsed', overflows && !expanded);
      button.hidden = !overflows;
      if (!overflows) {
        button.setAttribute('aria-expanded', 'false');
        button.textContent = button.dataset.expandLabel;
        if (document.activeElement === button) {
          target.tabIndex = -1;
          target.focus({ preventScroll: true });
        }
      }
    }
  };
  for (const button of expanders) {
    const label = button.textContent;
    button.dataset.expandLabel = label;
    button.addEventListener('click', () => {
      const target = document.getElementById(button.dataset.expand);
      const expanded = button.getAttribute('aria-expanded') !== 'true';
      button.setAttribute('aria-expanded', String(expanded));
      target.classList.toggle('is-collapsed', !expanded);
      button.textContent = expanded ? 'Show less' : label;
    });
  }
  addEventListener('resize', measureExpanders);

  // Ordinary section links remain usable without JavaScript. Enhance them
  // into tabs only after all panels exist; step URLs always open Activity.
  const sectionLinks = [...document.querySelectorAll('.section-links a')];
  const panels = [...document.querySelectorAll('.report-panel')];
  const tablist = document.querySelector('.section-links');
  if (tablist) {
    tablist.setAttribute('role', 'tablist');
    tablist.setAttribute('aria-label', 'Report sections');
  }
  sectionLinks.forEach((link) => link.setAttribute('role', 'tab'));
  panels.forEach((panel) => {
    panel.setAttribute('role', 'tabpanel');
    panel.setAttribute('aria-labelledby', 'tab-' + panel.id);
    panel.tabIndex = 0;
  });
  const showPanel = (id, navigate = false) => {
    if (!panels.some((panel) => panel.id === id)) return;
    const losingFocus = panels.some((panel) => panel.id !== id && panel.contains(document.activeElement));
    panels.forEach((panel) => { panel.hidden = panel.id !== id; });
    sectionLinks.forEach((link) => {
      const selected = link.getAttribute('aria-controls') === id;
      link.setAttribute('aria-selected', String(selected));
      link.tabIndex = selected ? 0 : -1;
    });
    if (losingFocus) sectionLinks.find((link) => link.getAttribute('aria-controls') === id).focus({ preventScroll: true });
    if (navigate && location.hash !== '#' + id) {
      try { history.pushState(null, '', '#' + id); } catch (e) { /* file: navigation can be restricted */ }
    }
    measureExpanders();
  };
  const panelFromHash = () => location.hash.startsWith('#step-') ? 'activity'
    : panels.some((panel) => '#' + panel.id === location.hash) ? location.hash.slice(1) : 'overview';
  showPanel(panelFromHash());
  sectionLinks.forEach((link, index) => {
    link.addEventListener('click', (event) => {
      if (event.ctrlKey || event.metaKey || event.shiftKey || event.button) return;
      event.preventDefault();
      showPanel(link.getAttribute('aria-controls'), true);
    });
    link.addEventListener('keydown', (event) => {
      if (event.altKey || event.ctrlKey || event.metaKey) return;
      const n = event.key === 'ArrowLeft' ? (index - 1 + sectionLinks.length) % sectionLinks.length
        : event.key === 'ArrowRight' ? (index + 1) % sectionLinks.length
        : event.key === 'Home' ? 0 : event.key === 'End' ? sectionLinks.length - 1 : null;
      if (n === null) return;
      event.preventDefault();
      showPanel(sectionLinks[n].getAttribute('aria-controls'), true);
      sectionLinks[n].focus();
    });
  });
  addEventListener('hashchange', () => showPanel(panelFromHash()));

  const preview = document.getElementById('preview-shot');
  const picker = document.getElementById('capture-select');
  if (preview && picker && picker.options.length) {
    const options = [...picker.options];
    const image = preview.querySelector('img');
    const full = document.querySelector('.preview-full');
    const caption = document.getElementById('preview-caption');
    const position = document.getElementById('preview-position');
    const prev = document.querySelector('.preview-prev');
    const next = document.querySelector('.preview-next');
    const stepLink = document.getElementById('preview-step');
    const history = document.querySelector('.screen-history');
    let index = picker.selectedIndex;
    document.querySelector('.capture-toolbar').hidden = false;
    picker.hidden = options.length === 1;
    const select = (n) => {
      index = Math.max(0, Math.min(options.length - 1, n));
      n = index;
      const capture = options[n];
      picker.selectedIndex = n;
      preview.href = capture.value;
      preview.dataset.caption = capture.dataset.caption;
      preview.setAttribute('aria-label', 'Open screenshot viewer: ' + capture.dataset.caption);
      image.src = capture.value;
      image.alt = capture.dataset.caption;
      for (const attr of ['width', 'height']) {
        if (capture.dataset[attr]) image.setAttribute(attr, capture.dataset[attr]);
        else image.removeAttribute(attr);
      }
      full.href = capture.value;
      caption.textContent = capture.dataset.caption;
      stepLink.href = '#step-' + capture.dataset.step;
      position.textContent = (n + 1) + ' / ' + options.length;
      prev.disabled = n === 0;
      next.disabled = n === options.length - 1;
    };
    prev.addEventListener('click', () => select(index - 1));
    next.addEventListener('click', () => select(index + 1));
    picker.addEventListener('change', () => select(picker.selectedIndex));
    history.addEventListener('keydown', (event) => {
      if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey || event.target.closest('select')) return;
      const direction = getComputedStyle(history).direction === 'rtl' ? -1 : 1;
      const n = event.key === 'ArrowLeft' ? index - direction : event.key === 'ArrowRight' ? index + direction
        : event.key === 'Home' ? 0 : event.key === 'End' ? options.length - 1 : null;
      if (n === null) return;
      event.preventDefault();
      select(n);
    });
    select(index);
  }

  for (const button of document.querySelectorAll('[data-copy]')) {
    button.addEventListener('click', async () => {
      const target = document.getElementById(button.dataset.copy);
      if (!target) return;
      const text = target.innerText;
      try {
        await navigator.clipboard.writeText(text);
      } catch (e) {
        const range = document.createRange();
        range.selectNodeContents(target);
        const selection = getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        document.execCommand('copy');
        selection.removeAllRanges();
      }
      // The icon cross-fades to a check (CSS); the words say it too.
      const label = button.querySelector('.btn-label');
      if (label && !label.dataset.label) label.dataset.label = label.textContent;
      button.classList.add('copied');
      if (label) label.textContent = 'Copied';
      clearTimeout(button.copiedTimer);
      button.copiedTimer = setTimeout(() => {
        button.classList.remove('copied');
        if (label) label.textContent = label.dataset.label;
      }, 1400);
    });
  }

  const toggle = document.getElementById('toggle-details');
  if (toggle) {
    toggle.addEventListener('click', () => {
      const all = [...document.querySelectorAll('.steps details')];
      const open = !all.every((d) => d.open);
      all.forEach((d) => { d.open = open; });
      toggle.textContent = open ? 'Collapse all' : 'Expand all';
    });
  }

  const viewer = document.getElementById('viewer');
  const shots = [...document.querySelectorAll('.steps a.shot')];
  if (viewer && viewer.showModal && shots.length) {
    const img = viewer.querySelector('img');
    const caption = viewer.querySelector('.viewer-caption');
    const count = viewer.querySelector('.viewer-count');
    const full = viewer.querySelector('.viewer-full');
    let index = 0;
    const show = (n) => {
      index = (n + shots.length) % shots.length;
      const shot = shots[index];
      img.src = shot.getAttribute('href');
      img.alt = shot.dataset.caption || '';
      caption.textContent = shot.dataset.caption || '';
      count.textContent = (index + 1) + ' / ' + shots.length;
      full.href = shot.getAttribute('href');
    };
    for (const link of document.querySelectorAll('a.shot')) {
      link.addEventListener('click', (event) => {
        if (event.ctrlKey || event.metaKey || event.shiftKey || event.button) return;
        event.preventDefault();
        const n = shots.findIndex((s) => s.href === link.href);
        show(n < 0 ? 0 : n);
        viewer.showModal();
      });
    }
    viewer.querySelector('.prev').addEventListener('click', () => show(index - 1));
    viewer.querySelector('.next').addEventListener('click', () => show(index + 1));
    viewer.querySelector('.close').addEventListener('click', () => viewer.close());
    viewer.addEventListener('click', (event) => {
      if (event.target === viewer || event.target.classList.contains('viewer-stage')) viewer.close();
    });
    viewer.addEventListener('keydown', (event) => {
      if (event.key === 'ArrowLeft') { event.preventDefault(); show(index - 1); }
      if (event.key === 'ArrowRight') { event.preventDefault(); show(index + 1); }
    });
    viewer.addEventListener('close', () => { img.removeAttribute('src'); });
  }

  const section = document.querySelector('section.steps');
  const steps = [...document.querySelectorAll('article.step')];
  const list = document.getElementById('nav-list');
  if (section && steps.length) {
    section.classList.add('single');
    const pager = document.getElementById('pager');
    const prev = document.getElementById('step-prev');
    const next = document.getElementById('step-next');
    const count = document.getElementById('pager-count');
    const links = list ? [...list.querySelectorAll('a')] : [];
    pager.hidden = false;
    let index = -1;
    const select = (n) => {
      n = Math.max(0, Math.min(steps.length - 1, n));
      if (n !== index) {
        if (index >= 0) steps[index].classList.remove('active');
        steps[n].classList.add('active');
        index = n;
      }
      count.textContent = (n + 1) + ' / ' + steps.length;
      prev.disabled = n === 0;
      next.disabled = n === steps.length - 1;
      links.forEach((a, i) => {
        a.classList.toggle('current', i === n);
        if (i === n) a.setAttribute('aria-current', 'step'); else a.removeAttribute('aria-current');
      });
      const link = links[n];
      if (link) {
        const top = link.offsetTop, bottom = top + link.offsetHeight;
        if (top < list.scrollTop || bottom > list.scrollTop + list.clientHeight) {
          list.scrollTop = top - list.clientHeight / 2;
        }
      }
    };
    // A step you picked goes in the address, so a link to it opens that step;
    // the one shown when the page opens doesn't, or reloading would jump to it.
    const go = (n, reveal) => {
      const crossing = document.getElementById('activity').hidden;
      showPanel('activity', crossing);
      select(n);
      if (crossing) {
        steps[index].tabIndex = -1;
        steps[index].focus({ preventScroll: true });
      }
      try { history.replaceState(null, '', '#' + steps[index].id); } catch (e) { /* some file: and data: pages refuse */ }
      if (reveal && section.getBoundingClientRect().top < 0) section.scrollIntoView({ block: 'start' });
    };
    const fromHash = () => steps.findIndex((s) => '#' + s.id === location.hash);
    document.addEventListener('click', (event) => {
      const a = event.target.closest('a[href^="#step-"]');
      if (!a || event.ctrlKey || event.metaKey || event.shiftKey || event.button) return;
      const n = steps.findIndex((s) => '#' + s.id === a.getAttribute('href'));
      if (n < 0) return;
      event.preventDefault();
      go(n, false);
      if (list && list.contains(a)) { if (section.getBoundingClientRect().top < 0) section.scrollIntoView({ block: 'start' }); }
      else section.scrollIntoView({ block: 'start' });
    });
    prev.addEventListener('click', () => go(index - 1, true));
    next.addEventListener('click', () => go(index + 1, true));
    addEventListener('hashchange', () => {
      const n = fromHash();
      if (n >= 0) { showPanel('activity'); select(n); section.scrollIntoView({ block: 'start' }); }
    });
    addEventListener('keydown', (event) => {
      if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) return;
      if (document.getElementById('activity').hidden) return;
      if ((viewer && viewer.open) || event.target.closest('input, textarea, select, [contenteditable]')) return;
      if (event.key === 'ArrowLeft' && index > 0) { event.preventDefault(); go(index - 1, true); }
      if (event.key === 'ArrowRight' && index < steps.length - 1) { event.preventDefault(); go(index + 1, true); }
    });
    const initial = fromHash();
    select(initial >= 0 ? initial : 0);
    // Opening a link to a step scrolls to it; a reload or Back keeps where you were.
    const entry = performance.getEntriesByType ? performance.getEntriesByType('navigation')[0] : null;
    const restoring = entry && (entry.type === 'reload' || entry.type === 'back_forward');
    if (initial >= 0 && !restoring) section.scrollIntoView({ block: 'start' });
  }
})();
"""

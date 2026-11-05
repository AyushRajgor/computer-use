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
from typing import Any, Optional

from rich.style import Style
from rich.text import Text

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


def _img(name: str, size: Optional[tuple[int, int]], alt: str) -> str:
    dims = f' width="{size[0]}" height="{size[1]}"' if size else ""
    src = _e(urllib.parse.quote(name))
    return f'<img loading="lazy" decoding="async" src="{src}"{dims} alt="{_e(alt)}">'


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


def _result_lines(action: dict[str, Any]) -> list[tuple[str, str]]:
    """What the agent sent back to Gemini after the action, as (kind, text)
    lines; kind is 'error', 'url', 'text' or 'muted'.

    The payload shapes come from agent.py: `{"url": ...}` plus the computer's
    metadata (desktop: `active_window`) after a browser action,
    `{"error": ...}` (and the URL if it could still be read) when the action
    raised, `"safety_acknowledgement": true` when you approved a safety
    confirmation, and whatever a custom function returned (`yield_to_user`:
    `user_response`, or `error` when nobody could answer)."""
    result = action["result"]
    if result is None:
        return [("muted", "Not run: the run stopped before this action.")]
    payload = result.get("payload")
    if not isinstance(payload, dict):
        if payload in (None, "", [], {}):
            return [("muted", "Only the screenshot." if action["screenshot"] else "Nothing.")]
        return [("text", f"Returned: {one_line(json.dumps(payload, ensure_ascii=False, default=str), 300)}")]
    rest = dict(payload)
    lines = []
    error = rest.pop("error", None)
    if action["failed"]:
        lines.append(("error", f"Failed: {error or 'the action raised an error'}"))
    elif error:  # a custom function that answered with an error, e.g. yield_to_user without a terminal
        lines.append(("error", f"Error reported: {error}"))
    url = rest.pop("url", None)
    if url:
        lines.append(("url", str(url)))
    window = rest.pop("active_window", None)
    if window:
        lines.append(("text", f"Active window: {window}"))
    if rest.pop("safety_acknowledgement", None):
        lines.append(("text", "Safety confirmation: you (or --safety auto-confirm) approved this action."))
    if "user_response" in rest:
        reply = rest.pop("user_response")
        lines.append(("text", f"Your reply: {reply}" if reply else "No reply from you."))
    for key, value in rest.items():
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
        lines.append(("text", f"{key}: {one_line(text, 300)}"))
    if not lines:
        lines.append(("muted", "Only the screenshot." if action["screenshot"] else "Nothing."))
    return lines


def _step_views(
    steps: list[dict[str, Any]], session: Session, tz: Optional[datetime.tzinfo]
) -> list[dict[str, Any]]:
    start = session.started_at
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
        f'<div class="outcome"><div class="outcome-icon">{_icon(icon)}</div><div class="outcome-body">',
        f'<div class="outcome-head"><h2 id="outcome-title">{_e(heading)}</h2>',
        f'<div class="outcome-actions no-print">{"".join(buttons)}</div>' if buttons else "",
        "</div>",
        f'<div id="outcome-text" class="{"outcome-mono" if mono else "outcome-prose"}">{_e(body)}</div>',
    ]
    if note:
        out.append(f'<p class="outcome-note">{_e(note)}</p>')
    if answer and status != "completed":
        out.append(f'<p class="outcome-note"><strong>Last message:</strong> {_e(answer)}</p>')
    out.append("</div></div>")
    return "".join(out)


def _copy_button(target: str, label: str) -> str:
    """A button that copies the text of the element with id `target`. Both icons
    stay in the DOM so the copy icon can cross-fade to a check when it's done."""
    return (
        f'<button class="btn no-print" type="button" data-copy="{_e(target)}">'
        f'<span class="swap" aria-hidden="true">{_icon("copy")}{_icon("check")}</span>'
        f'<span class="btn-label" aria-live="polite">{_e(label)}</span></button>'
    )


def _figures(parts: list[str]) -> str:
    """'Gemini 2m 52s · actions 23.1s': a line may wrap between figures, never
    inside one, and the dot stays at the end of a line, not the start."""
    return " · ".join(part.replace(" ", " ") for part in parts if part)


def _stats(totals: dict[str, Any]) -> str:
    """The totals, as one row of figures (the run's start and end are in the header)."""
    duration, model, actions = totals["duration_s"], totals["model_s"], totals["actions_s"]
    tiles = []

    known = (model or 0) + (actions or 0)
    total = duration if isinstance(duration, (int, float)) and duration > 0 else known
    split_text = ""
    if total:
        split = [("Gemini", model), ("actions", actions)]
        if isinstance(duration, (int, float)):
            split.append(("other", max(0.0, total - known)))
        split_text = _figures(
            [f"{name} {format_duration(value)}" for name, value in split if value and value >= 0.05]
        )
    tiles.append(
        ("Duration", format_duration(duration if duration is not None else (known or None)), _e(split_text))
    )

    steps = totals["steps"]
    per_step = ""
    if isinstance(steps, int) and steps and total:
        per_step = f"about {format_seconds(total / steps)} per step"
    tiles.append(("Steps", format_number(steps), _e(per_step)))

    calls = sum(totals["tool_calls"].values())
    failed = totals["tool_errors"]
    top = sorted(totals["tool_calls"].items(), key=lambda kv: -kv[1])[:3]
    sub = _e(_figures([f"{name} {n}" for name, n in top]))
    if failed:
        sub = f'<span class="bad-text">{failed} failed</span>' + (f" · {sub}" if sub else "")
    tiles.append(("Actions", format_number(calls), sub))

    usage = totals["usage"]
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
    tiles.append(("Tokens", format_number(usage.get("total_tokens")), _e(_figures(parts))))

    retries = {k: n for k, n in (totals.get("retries") or {}).items() if n}
    if retries:
        tiles.append(
            (
                "Retries",
                format_number(sum(retries.values())),
                _e(", ".join(f"{n} {kind.replace('_', ' ')}" for kind, n in retries.items())),
            )
        )
    cells = "".join(
        f'<div class="metric"><dt>{_e(label)}</dt><dd class="metric-value">{_e(value)}</dd>'
        + (f'<dd class="metric-sub">{sub}</dd>' if sub else "")
        + "</div>"
        for label, value, sub in tiles
    )
    # The clip hides the outer edges of the cells' dividers, however the row wraps.
    return f'<div class="metrics-clip"><dl class="metrics" aria-label="Totals">{cells}</dl></div>'


def _filmstrip(views: list[dict[str, Any]], session: Session) -> str:
    shots = [(v, a) for v in views for a in v["actions"] if a["screenshot"]]
    if not shots:
        return ""
    items = "".join(
        f'<a class="thumb shot" href="{_e(urllib.parse.quote(a["screenshot"]))}"'
        f' data-caption="Step {_e(v["number"])} · {_e(a["title"])}" title="Step {_e(v["number"])} · {_e(a["title"])}">'
        f'{_img(a["screenshot"], a["size"], "")}<span class="thumb-n">{_e(v["number"])}</span></a>'
        for v, a in shots
    )
    return (
        '<section class="filmstrip no-print" aria-labelledby="shots-title">'
        f'<div class="section-head"><h2 id="shots-title">Screenshots <span class="count">{len(shots)}</span></h2>'
        '<span class="hint">Click one to look through them; ← and → move between them.</span></div>'
        f'<div class="strip">{items}</div></section>'
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
        '<nav class="steps-nav no-print" aria-label="Steps"><div class="nav-head">Steps</div>'
        f'<ol id="nav-list">{"".join(items)}</ol></nav>'
    )


def _link(url: str) -> str:
    """A URL from the log as text, clickable only when it is a web address."""
    text = _e(url)
    if urllib.parse.urlsplit(url).scheme in ("http", "https"):
        return f'<a href="{text}" rel="noopener noreferrer" target="_blank">{text}</a>'
    return text


def _action_html(action: dict[str, Any], view: dict[str, Any], run_start: Optional[datetime.datetime]) -> str:
    number, duration = view["number"], action["duration"]
    lines = []
    for kind, text in _result_lines(action):
        if kind == "url":
            lines.append(f'<li class="line-url">Page after: {_link(text)}</li>')
        else:
            lines.append(f'<li class="line-{kind}">{_e(text)}</li>')
    meta = []
    when = " → ".join(_clock(t) for t in (action["began"], action["ended"]) if t)
    if when:
        offset = _offset(run_start, action["began"] or action["ended"])
        meta.append(when + (f" ({offset})" if offset else ""))
    if duration is not None:
        meta.append(f"took {format_seconds(duration)}")
    if action["screenshot"]:
        caption = f"Step {number} · {action['title']}"
        shot = (
            f'<a class="shot thumb-shot" href="{_e(urllib.parse.quote(action["screenshot"]))}" data-caption="{_e(caption)}"'
            f' title="Open the screenshot">{_img(action["screenshot"], action["size"], "Screen after: " + action["title"])}'
            f'<span class="thumb-cap">Screenshot</span></a>'
        )
    else:
        shot = '<div class="thumb-shot thumb-none">No screenshot</div>'
    result = action["result"]
    payload_html = (
        f'<pre class="code">{_json(result.get("payload"))}</pre>' if result else '<p class="muted">Not run.</p>'
    )
    return (
        f'<li class="action{" failed" if action["failed"] else ""}">'
        f'<div class="action-head"><span class="action-icon">{_icon("alert" if action["failed"] else action["icon"])}</span>'
        f'<div class="action-text"><div class="action-title">{_e(action["title"])}</div>'
        f'<code class="action-code">{_e(action["signature"])}</code></div>'
        f'<span class="chip">{_e(format_seconds(duration)) if duration is not None else "not run"}</span></div>'
        '<div class="reply"><div class="reply-text"><div class="label">Sent back to Gemini</div>'
        f'<ul class="lines">{"".join(lines)}</ul>'
        + (f'<div class="action-meta">{_e(" · ".join(meta))}</div>' if meta else "")
        + f"</div>{shot}</div>"
        '<details class="raw"><summary>Raw call and result (JSON)</summary><div class="cols">'
        f'<div><div class="label">Arguments</div><pre class="code">{_json(action["args"])}</pre></div>'
        f'<div><div class="label">Result</div>{payload_html}</div></div></details></li>'
    )


def _step_html(view: dict[str, Any], run_start: Optional[datetime.datetime]) -> str:
    step, number = view["step"], view["number"]
    usage = step.get("usage") or {}
    began, ended = _clock(view["began"]), _clock(view["ended"])
    when = " → ".join(x for x in (began, ended) if x)
    if when and view["offset"]:
        when += f" ({view['offset']})"
    meta = [when]
    if isinstance(step.get("model_s"), (int, float)):
        meta.append(f"Gemini {format_seconds(step['model_s'])}")
    if isinstance(step.get("actions_s"), (int, float)):
        meta.append(f"actions {format_seconds(step['actions_s'])}")
    if isinstance(usage.get("total_tokens"), (int, float)):
        meta.append(f"{format_number(usage['total_tokens'])} tokens")
    retries = step.get("retries") or {}
    empty, network = retries.get("empty_response") or 0, len(retries.get("network") or [])
    if empty or network:
        meta.append(f"retried {empty + network}×")
    out = [
        f'<article class="step step-{view["kind"]}" id="step-{_e(number)}">'
        f'<header class="step-head"><span class="num">{_e(number)}</span>'
        f'<div><h3>Step {_e(number)}{" · Final answer" if view["kind"] == "final" else ""}</h3>'
        f'<div class="step-meta">{_e(" · ".join(m for m in meta if m))}</div></div></header>'
    ]
    if view["problem"]:
        out.append(f'<div class="alert">{_icon("alert")}<span>{_e(view["problem"])}</span></div>')
    if step.get("text"):
        cls = "answer" if view["kind"] == "final" else "reasoning"
        out.append(f'<div class="{cls}">{_e(step["text"])}</div>')
    if step.get("thoughts"):
        out.append(
            '<details class="thoughts"><summary>Model’s thinking</summary>'
            f'<div class="thoughts-text">{_e(step["thoughts"])}</div></details>'
        )
    if view["actions"]:
        out.append(
            '<ol class="actions">' + "".join(_action_html(a, view, run_start) for a in view["actions"]) + "</ol>"
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
        '<section class="card settings" aria-labelledby="settings-title">'
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
    '<span class="viewer-tools"><a class="viewer-btn viewer-full" target="_blank" rel="noopener" title="Open full size">'
    + _icon("external")
    + '</a><button class="viewer-btn close" type="button" title="Close (Esc)">'
    + _icon("x")
    + '</button></span></div><div class="viewer-stage">'
    '<button class="viewer-btn prev" type="button" title="Previous (←)">'
    + _icon("left")
    + '</button><img alt=""><button class="viewer-btn next" type="button" title="Next (→)">'
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
        when,
        run.get("model"),
        ENVIRONMENTS.get(env, env),
    ]
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
        '<header class="top"><div class="wrap">',
        f'<p class="eyebrow">Gemini Computer Use · Session <span class="mono">{_e(session.id)}</span></p>',
        f"<h1>{_e(title)}</h1>",
        f'<div class="meta-row"><span class="badge tone-{tone}">{_icon(icon)}{_e(label)}</span>'
        '<ul class="facts-line">' + "".join(f"<li>{_e(f)}</li>" for f in facts if f) + "</ul></div>",
        "</div></header><main class='wrap'>",
        f'<section class="summary tone-{tone}" aria-labelledby="outcome-title">',
        _outcome(session, views),
        _stats(totals),
        "</section>",
    ]
    if (query and query != first_line) or len(first_line) > 160:
        out.append(
            '<section class="card task" aria-labelledby="task-title"><div class="section-head">'
            f'<h2 id="task-title">Task</h2>{_copy_button("task-text", "Copy task")}</div>'
            f'<div id="task-text" class="task-text">{_e(query)}</div></section>'
        )
    out.append(_filmstrip(views, session))
    if views:
        out.append(
            f'<div class="layout">{_nav(views)}<section class="steps" aria-labelledby="steps-title">'
            '<div class="section-head"><h2 id="steps-title">Step by step '
            f'<span class="count">{len(views)}</span></h2>'
            '<button class="btn no-print" type="button" id="toggle-details">Expand all</button></div>'
            + "".join(_step_html(v, start) for v in views)
            + "</section></div>"
        )
    else:
        out.append(
            '<section class="card empty"><div class="section-head"><h2>Step by step</h2></div>'
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


def report_link(path: "str | pathlib.Path") -> Text:
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
            return Text(f"Report: runs/{inside.as_posix()}  (in the project folder on your computer)")
    url = path.as_uri()
    return Text.assemble("Report: ", (url, Style(link=url)))


# ---------------------------------------------------------------------------
# style and script
# ---------------------------------------------------------------------------

_CSS = """
:root {
  color-scheme: light dark;
  --bg: #f4f5f7; --surface: #ffffff; --sunken: #f0f2f5; --hover: #e9ecf1;
  --text: #15171c; --muted: #5c6370; --faint: #8a919c; --line: #e1e4e9; --line-strong: #cdd2da;
  --accent: #2563eb; --accent-soft: #e8efff;
  --ok: #137a45; --ok-soft: #e5f4ec; --bad: #c22d2d; --bad-soft: #fdecea;
  --warn: #965a00; --warn-soft: #fff2d9; --idle: #4b5563; --idle-soft: #eceef2;
  --shadow: 0 1px 2px rgba(16, 24, 40, .04), 0 1px 3px rgba(16, 24, 40, .05);
  --mono: ui-monospace, "Cascadia Code", "SF Mono", Menlo, Consolas, monospace;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0e1014; --surface: #171a20; --sunken: #1d2129; --hover: #252a33;
    --text: #e7e9ee; --muted: #a1a8b4; --faint: #737b88; --line: #2a2f38; --line-strong: #3a404b;
    --accent: #7aa5ff; --accent-soft: #1c2840;
    --ok: #4fd08a; --ok-soft: #12291d; --bad: #ff7d7d; --bad-soft: #321718;
    --warn: #f2b84f; --warn-soft: #2e2210; --idle: #b4bcc8; --idle-soft: #232831;
    --shadow: none;
  }
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
@media (prefers-reduced-motion: reduce) { html { scroll-behavior: auto; } }
body { margin: 0; background: var(--bg); color: var(--text);
  font: 15px/1.55 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  -webkit-font-smoothing: antialiased; }
a { color: var(--accent); }
code, .mono { font-family: var(--mono); font-size: .9em; }
.wrap { max-width: 1180px; margin: 0 auto; padding: 0 20px; }
.i { width: 16px; height: 16px; flex: none; }
.muted { color: var(--muted); }
.bad-text { color: var(--bad); font-weight: 600; }
.tone-ok { --tone: var(--ok); --tone-soft: var(--ok-soft); }
.tone-bad { --tone: var(--bad); --tone-soft: var(--bad-soft); }
.tone-warn { --tone: var(--warn); --tone-soft: var(--warn-soft); }
.tone-idle { --tone: var(--idle); --tone-soft: var(--idle-soft); }

/* header: what ran, its status and when */
.top { background: var(--surface); border-bottom: 1px solid var(--line); padding: 26px 0 22px; }
.eyebrow { margin: 0; color: var(--muted); font-size: 13px; }
h1 { margin: 6px 0 12px; font-size: 24px; line-height: 1.3; font-weight: 650; letter-spacing: -.01em;
  text-wrap: balance; overflow-wrap: anywhere; }
.meta-row { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 14px; }
.badge { display: inline-flex; align-items: center; gap: 6px; padding: 3px 11px 3px 8px; border-radius: 999px;
  background: var(--tone-soft); color: var(--tone); font-size: 13px; font-weight: 600; line-height: 1.5; }
.badge .i { width: 14px; height: 14px; }
.facts-line { display: flex; flex-wrap: wrap; gap: 4px 0; margin: 0; padding: 0; list-style: none;
  color: var(--muted); font-size: 13.5px; font-variant-numeric: tabular-nums; }
.facts-line li:not(:last-child)::after { content: "·"; margin: 0 8px; color: var(--faint); }
main.wrap { padding-top: 24px; padding-bottom: 56px; }

/* shared */
h2 { margin: 0; font-size: 16px; font-weight: 650; }
.section-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 12px; }
.count { display: inline-block; min-width: 22px; margin-left: 4px; padding: 0 7px; border-radius: 999px;
  background: var(--sunken); color: var(--muted); font-size: 12.5px; font-weight: 600; text-align: center; }
.hint { color: var(--faint); font-size: 13px; }
.card { background: var(--surface); border: 1px solid var(--line); border-radius: 12px; padding: 18px 20px;
  box-shadow: var(--shadow); margin-top: 16px; }
.btn { position: relative; display: inline-flex; align-items: center; gap: 6px; padding: 5px 12px;
  border: 1px solid var(--line-strong); border-radius: 8px; background: var(--surface); color: var(--text);
  font: 500 13px/1.4 inherit; text-decoration: none; white-space: nowrap; cursor: pointer;
  transition-property: background-color, scale; transition-duration: .15s; transition-timing-function: ease-out; }
.btn:hover { background: var(--hover); }
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
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.code { margin: 0; padding: 10px 12px; border-radius: 8px; background: var(--sunken); overflow: auto; max-height: 420px;
  white-space: pre-wrap; overflow-wrap: anywhere; font: 12.5px/1.5 var(--mono); }
.label { margin-bottom: 4px; color: var(--muted); font-size: 12.5px; }
details > summary { cursor: pointer; color: var(--muted); font-size: 13.5px; width: fit-content; list-style: none;
  display: flex; align-items: center; gap: 6px; }
details > summary::-webkit-details-marker { display: none; }
details > summary::before { content: ""; width: 6px; height: 6px; border: solid currentColor; border-width: 0 1.5px 1.5px 0;
  transform: rotate(-45deg); transition: transform .15s; }
details[open] > summary::before { transform: rotate(45deg); }
details > summary:hover { color: var(--text); }

/* summary: how the run ended, then its totals, in one card */
.summary { background: var(--surface); border: 1px solid var(--line); border-radius: 12px; box-shadow: var(--shadow);
  overflow: hidden; }
.outcome { display: grid; grid-template-columns: 32px minmax(0, 1fr); gap: 14px; padding: 20px 22px;
  background: color-mix(in srgb, var(--tone-soft) 65%, var(--surface)); }
.outcome-icon { display: grid; place-items: center; width: 32px; height: 32px; border-radius: 50%;
  background: var(--tone); color: var(--surface); }
.outcome-icon .i { width: 17px; height: 17px; stroke-width: 2.5; }
.outcome-body { min-width: 0; }
.outcome-head { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 16px; min-height: 32px; }
.outcome-head h2 { flex: 1 1 260px; min-width: 0; font-size: 17px; line-height: 1.35; text-wrap: balance; }
.outcome-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.outcome-prose { margin-top: 6px; white-space: pre-wrap; overflow-wrap: anywhere; text-wrap: pretty;
  font-size: 15.5px; line-height: 1.6; }
.outcome-mono { margin-top: 10px; padding: 10px 12px; border-radius: 8px; white-space: pre-wrap; overflow-wrap: anywhere;
  background: var(--surface); border: 1px solid color-mix(in srgb, var(--tone) 28%, transparent);
  font: 13px/1.55 var(--mono); }
.outcome-note { margin: 10px 0 0; color: var(--muted); white-space: pre-wrap; text-wrap: pretty; }
.metrics-clip { overflow: hidden; border-top: 1px solid var(--line); }
.metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); margin: -1px 0 0 -1px; }
.metric { min-width: 0; padding: 15px 22px 17px; border-left: 1px solid var(--line); border-top: 1px solid var(--line); }
.metric dt { color: var(--muted); font-size: 12.5px; font-weight: 500; }
.metric-value { margin: 3px 0 0; font-size: 22px; line-height: 1.25; font-weight: 650; letter-spacing: -.01em;
  font-variant-numeric: tabular-nums; }
.metric-sub { margin: 5px 0 0; color: var(--muted); font-size: 12.5px; line-height: 1.45;
  font-variant-numeric: tabular-nums; text-wrap: pretty; overflow-wrap: anywhere; }

/* task */
.task .section-head { margin-bottom: 10px; }
.task-text { white-space: pre-wrap; overflow-wrap: anywhere; text-wrap: pretty; line-height: 1.6; }

/* screenshots strip */
.filmstrip { margin-top: 28px; }
.strip { display: flex; gap: 10px; overflow-x: auto; padding: 2px 2px 10px; scroll-snap-type: x proximity; }
.thumb { position: relative; flex: none; width: 176px; aspect-ratio: 16 / 10; border-radius: 8px; overflow: hidden;
  border: 1px solid var(--line); background: var(--sunken); scroll-snap-align: start; }
.thumb img { width: 100%; height: 100%; object-fit: cover; object-position: top; display: block; }
.thumb:hover { border-color: var(--accent); }
.thumb-n { position: absolute; left: 6px; bottom: 6px; min-width: 22px; padding: 1px 6px; border-radius: 6px;
  background: rgba(15, 17, 21, .78); color: #fff; font-size: 12px; font-weight: 600; text-align: center; }

/* steps */
.layout { display: grid; grid-template-columns: minmax(0, 1fr); gap: 24px; margin-top: 28px; }
@media (min-width: 1000px) { .layout { grid-template-columns: 340px minmax(0, 1fr); } }
.steps-nav { display: none; }
@media (min-width: 1000px) {
  .steps-nav { display: block; position: sticky; top: 16px; align-self: start; max-height: calc(100vh - 32px);
    background: var(--surface); border: 1px solid var(--line); border-radius: 12px; box-shadow: var(--shadow);
    overflow: hidden; }
}
.nav-head { padding: 12px 14px 8px; font-size: 13px; font-weight: 650; color: var(--muted); }
.steps-nav ol { position: relative; margin: 0; padding: 0 6px 8px; list-style: none; overflow-y: auto;
  max-height: calc(100vh - 80px); }
.steps-nav a { display: grid; grid-template-columns: 24px minmax(0, 1fr); gap: 10px; align-items: start;
  padding: 7px 8px; border-radius: 8px; color: var(--text); text-decoration: none; }
.steps-nav a:hover { background: var(--hover); }
.steps-nav a.current { background: var(--accent-soft); }
.num { display: inline-grid; place-items: center; width: 24px; height: 24px; border-radius: 50%;
  background: var(--sunken); color: var(--muted); font-size: 12px; font-weight: 650; font-variant-numeric: tabular-nums; }
.nav-failed .num, .step-failed .step-head .num { background: var(--bad-soft); color: var(--bad); }
.nav-final .num, .step-final .step-head .num { background: var(--ok-soft); color: var(--ok); }
.nav-main { min-width: 0; }
.nav-title { display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; overflow: hidden;
  font-size: 14px; line-height: 1.35; overflow-wrap: anywhere; }
.nav-sub { display: block; margin-top: 2px; color: var(--faint); font-size: 12px; font-variant-numeric: tabular-nums; }
.step { background: var(--surface); border: 1px solid var(--line); border-radius: 12px; padding: 18px 20px;
  box-shadow: var(--shadow); margin-bottom: 14px; scroll-margin-top: 16px; }
.step-failed { border-color: color-mix(in srgb, var(--bad) 35%, var(--line)); }
.step-final { border-color: color-mix(in srgb, var(--ok) 35%, var(--line)); }
.step-head { display: flex; align-items: center; gap: 12px; }
.step-head .num { width: 30px; height: 30px; font-size: 13px; }
.step-head h3 { margin: 0; font-size: 15.5px; font-weight: 650; }
.step-meta { color: var(--muted); font-size: 13px; font-variant-numeric: tabular-nums; }
.reasoning { margin-top: 12px; white-space: pre-wrap; overflow-wrap: anywhere; }
.answer { margin-top: 12px; padding: 12px 14px; border-radius: 8px; background: var(--ok-soft);
  white-space: pre-wrap; overflow-wrap: anywhere; }
.alert { display: flex; gap: 8px; align-items: flex-start; margin-top: 12px; padding: 10px 12px; border-radius: 8px;
  background: var(--bad-soft); color: var(--bad); font-size: 14px; overflow-wrap: anywhere; }
.alert .i { margin-top: 2px; }
.thoughts { margin-top: 10px; }
.thoughts-text { margin-top: 8px; padding: 10px 12px; border-radius: 8px; background: var(--sunken);
  color: var(--muted); white-space: pre-wrap; overflow-wrap: anywhere; font-size: 14px; }
.actions { margin: 14px 0 0; padding: 0; list-style: none; }
.action { padding: 14px 14px 12px; border: 1px solid var(--line); border-radius: 10px; background: var(--sunken); }
.action + .action { margin-top: 10px; }
.action.failed { border-color: color-mix(in srgb, var(--bad) 45%, var(--line)); }
.action-head { display: grid; grid-template-columns: 32px minmax(0, 1fr) auto; gap: 12px; align-items: start; }
.action-icon { display: grid; place-items: center; width: 32px; height: 32px; border-radius: 8px;
  background: var(--accent-soft); color: var(--accent); }
.action.failed .action-icon { background: var(--bad-soft); color: var(--bad); }
.action-title { font-weight: 600; overflow-wrap: anywhere; }
.action-code { display: block; margin-top: 2px; color: var(--muted); font-size: 12.5px; overflow-wrap: anywhere; }
.chip { padding: 2px 8px; border-radius: 999px; background: var(--surface); border: 1px solid var(--line); color: var(--muted);
  font-size: 12.5px; font-variant-numeric: tabular-nums; white-space: nowrap; }
.reply { display: grid; grid-template-columns: minmax(0, 1fr) 224px; gap: 14px; align-items: start; margin: 12px 0 0 44px; }
.reply-text { min-width: 0; }
.lines { margin: 0; padding: 0; list-style: none; font-size: 14px; }
.lines li { overflow-wrap: anywhere; }
.lines li + li { margin-top: 3px; }
.line-error { padding: 8px 10px; border-radius: 8px; background: var(--bad-soft); color: var(--bad); font-weight: 600;
  white-space: pre-wrap; }
.line-error + li { margin-top: 8px; }
.line-muted { color: var(--muted); }
.line-url a { color: var(--accent); text-decoration: none; }
.line-url a:hover { text-decoration: underline; }
.action-meta { margin-top: 8px; color: var(--faint); font-size: 12.5px; font-variant-numeric: tabular-nums; }
.thumb-shot { display: block; position: relative; border-radius: 8px; overflow: hidden; border: 1px solid var(--line);
  background: var(--surface); cursor: zoom-in; text-decoration: none; }
.thumb-shot img { display: block; width: 100%; height: auto; }
.thumb-shot:hover { border-color: var(--accent); }
.thumb-cap { position: absolute; left: 6px; bottom: 6px; padding: 1px 7px; border-radius: 6px;
  background: rgba(15, 17, 21, .78); color: #fff; font-size: 11.5px; font-weight: 600; }
.thumb-none { display: grid; place-items: center; aspect-ratio: 16 / 10; color: var(--faint); font-size: 12.5px; cursor: default; }
.raw { margin: 12px 0 0 44px; }
.cols { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 12px; margin-top: 10px; }
@media (max-width: 760px) {
  .cols { grid-template-columns: minmax(0, 1fr); }
  .reply, .raw { margin-left: 0; }
  .reply { grid-template-columns: minmax(0, 1fr) 128px; gap: 10px; }
  .action { padding: 12px; }
}

/* settings and footer */
.settings { margin-top: 12px; }
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
  border: 0; background: transparent; color: #fff; }
.viewer::backdrop { background: rgba(8, 10, 14, .9); }
.viewer-inner { display: grid; grid-template-rows: auto minmax(0, 1fr); height: 100%; }
.viewer-bar { display: flex; align-items: center; gap: 14px; padding: 12px 16px; font-size: 14px; }
.viewer-count { color: rgba(255, 255, 255, .6); font-variant-numeric: tabular-nums; white-space: nowrap; }
.viewer-caption { flex: 1; min-width: 0; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.viewer-tools { display: flex; gap: 8px; }
.viewer-stage { display: grid; grid-template-columns: 56px minmax(0, 1fr) 56px; align-items: center; min-height: 0; padding-bottom: 16px; }
.viewer-stage img { display: block; max-width: 100%; max-height: calc(100vh - 90px); margin: auto; border-radius: 6px;
  box-shadow: 0 10px 40px rgba(0, 0, 0, .5); background: #fff; }
.viewer-btn { display: inline-grid; place-items: center; width: 40px; height: 40px; margin: auto; border: 0; border-radius: 50%;
  background: rgba(255, 255, 255, .12); color: #fff; cursor: pointer; }
.viewer-btn:hover { background: rgba(255, 255, 255, .22); }
.viewer-btn .i { width: 20px; height: 20px; }

@media (max-width: 640px) {
  .wrap { padding-left: 16px; padding-right: 16px; }
  main.wrap { padding-top: 18px; padding-bottom: 40px; }
  h1 { font-size: 20px; }
  .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .metric { padding: 13px 16px 15px; }
  .card, .step, .outcome { padding: 14px; }
  .outcome { gap: 12px; }
  /* Narrow: the buttons go under the text they act on, not between it and its heading. */
  .outcome-body { display: flex; flex-direction: column; }
  .outcome-head { display: contents; }
  .outcome-head h2 { flex: none; padding-top: 4px; }
  .outcome-actions { order: 1; margin-top: 14px; }
  .viewer-stage { grid-template-columns: 40px minmax(0, 1fr) 40px; }
}
@media print {
  body { background: #fff; }
  .no-print, .viewer { display: none !important; }
  .layout { display: block; }
  .step, .card, .summary, .action { box-shadow: none; break-inside: avoid; }
}
"""

_JS = """
(() => {
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
        const n = shots.findIndex((s) => s.getAttribute('href') === link.getAttribute('href'));
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

  const list = document.getElementById('nav-list');
  if (list && 'IntersectionObserver' in window) {
    const links = new Map([...list.querySelectorAll('a')].map((a) => [a.getAttribute('href').slice(1), a]));
    const last = [...links.values()].pop();
    const mark = (link) => {
      if (!link || link.classList.contains('current')) return;
      links.forEach((a) => a.classList.remove('current'));
      link.classList.add('current');
      const top = link.offsetTop, bottom = top + link.offsetHeight;
      if (top < list.scrollTop || bottom > list.scrollTop + list.clientHeight) {
        list.scrollTop = top - list.clientHeight / 2;
      }
    };
    const atBottom = () => innerHeight + scrollY >= document.documentElement.scrollHeight - 4;
    const observer = new IntersectionObserver((entries) => {
      if (atBottom()) return;
      for (const entry of entries) {
        if (entry.isIntersecting) mark(links.get(entry.target.id));
      }
    }, { rootMargin: '-15% 0px -75% 0px' });
    document.querySelectorAll('article.step').forEach((step) => observer.observe(step));
    // The last steps can't scroll up to the band above; at the bottom, it's the last one.
    addEventListener('scroll', () => { if (atBottom()) mark(last); }, { passive: true });
  }
})();
"""

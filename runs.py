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
"""See the agent's sessions: what ran, how long it took, what it used.

    python runs.py                   # every session, newest first
    python runs.py 1                 # one session: # from that list (1 = newest)
    python runs.py latest            # the newest session
    python runs.py 20261105-1415     # a session id, or enough of its start or end
    python runs.py latest --html     # rebuild its report.html page and open it

Sessions are saved by `python main.py` (on by default; --no-log turns it off),
which also writes each session's report.html when the run ends.
"""
import argparse
import pathlib
import sys
import webbrowser
from typing import Any, Optional

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from report import report_link, write_report
from telemetry import (
    REPORT_FILE,
    RUNS_DIR,
    Session,
    format_duration,
    format_number,
    format_seconds,
    format_tokens,
    list_sessions,
    one_line,
)

STATUS_STYLE = {
    "completed": "green",
    "unfinished": "yellow",
    "max_steps": "yellow",
    "terminated": "yellow",
    "error": "red",
}


def find_session(sessions: list[Session], selector: str) -> Session:
    """`selector`: a # from the list (1 = newest), 'latest', or a session id
    (or enough of its start or end to be unique)."""
    if not sessions:
        raise LookupError("There are no sessions yet.")
    if selector in ("latest", "last"):
        return sessions[0]
    if selector.isdigit() and 1 <= int(selector) <= len(sessions):
        return sessions[int(selector) - 1]
    for session in sessions:
        if session.id == selector:
            return session
    matches = [s for s in sessions if s.id.startswith(selector) or s.id.endswith(selector)]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise LookupError(f"No session matches {selector!r}. List them with: python runs.py")
    shown = ", ".join(s.id for s in matches[:5]) + (", ..." if len(matches) > 5 else "")
    raise LookupError(f"{selector!r} matches {len(matches)} sessions ({shown}). Use more of the id.")


# ---------------------------------------------------------------------------
# formatting helpers
# ---------------------------------------------------------------------------


def _tool_breakdown(tool_calls: dict[str, int]) -> str:
    counts = sorted(tool_calls.items(), key=lambda kv: -kv[1])
    return ", ".join(f"{name} {n}" for name, n in counts) or "-"


def _note(step: dict[str, Any]) -> str:
    intents = [c.get("args", {}).get("intent") for c in step.get("calls") or []]
    return "; ".join(i for i in intents if i) or step.get("text") or ""


def _settings_line(session: Session) -> str:
    run = session.run
    parts = [run.get("model"), run.get("api"), run.get("environment")]
    settings = run.get("settings") or {}
    if settings.get("env"):
        parts[-1] = settings["env"]
    return " · ".join(p for p in parts if p) or "-"


# ---------------------------------------------------------------------------
# terminal views
# ---------------------------------------------------------------------------


def show_list(console: Console, sessions: list[Session], root: pathlib.Path, limit: int) -> None:
    if not sessions:
        console.print(f"No sessions in {escape(str(root))} yet. They're saved each time the agent runs.")
        return
    table = Table(title=f"Sessions in {escape(str(root))}", title_justify="left")
    table.add_column("#", justify="right", style="dim")
    table.add_column("Session", no_wrap=True)
    table.add_column("Started", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("Steps", justify="right")
    table.add_column("Time", justify="right", no_wrap=True)
    table.add_column("Tokens", justify="right")
    table.add_column("Tools", justify="right")
    table.add_column("Task", overflow="ellipsis", no_wrap=True, max_width=60)
    shown = sessions[:limit] if limit > 0 else sessions
    for i, s in enumerate(shown, 1):
        totals = s.totals()
        errors = totals["tool_errors"]
        tools = str(sum(totals["tool_calls"].values())) + (f" ({errors} failed)" if errors else "")
        started = s.started_at
        table.add_row(
            str(i),
            escape(s.id),
            started.strftime("%b %d %H:%M") if started else "-",
            f"[{STATUS_STYLE.get(s.status, 'white')}]{escape(s.status)}[/]",
            format_number(totals["steps"]),
            format_duration(totals["duration_s"]),
            format_number(totals["usage"].get("total_tokens")),
            tools,
            escape(one_line(s.query, 60)),
        )
    console.print(table)
    if len(shown) < len(sessions):
        console.print(f"[dim]{len(sessions) - len(shown)} older session(s) not shown (--limit 0 shows all).[/]")
    console.print("[dim]One session: python runs.py <# or session id>   Its page with screenshots: add --html[/]")


def show_session(console: Console, session: Session) -> None:
    steps = session.steps()
    totals = session.totals(steps)
    result = session.result or {}
    style = STATUS_STYLE.get(session.status, "white")
    console.print(f"[bold]Session {escape(session.id)}[/]  [{style}]{escape(session.status)}[/]")

    overview = Table.grid(padding=(0, 2))
    overview.add_column(style="bold", no_wrap=True)
    overview.add_column()
    started = session.started_at
    rows = [
        ("Task", one_line(session.query, 300) or "-"),
        ("Model", _settings_line(session)),
        ("Started", started.strftime("%Y-%m-%d %H:%M:%S") if started else "-"),
        (
            "Time",
            f"{format_duration(totals['duration_s'])} total: "
            f"{format_duration(totals['model_s'])} waiting for Gemini, "
            f"{format_duration(totals['actions_s'])} running actions",
        ),
        ("Steps", format_number(totals["steps"])),
        (
            "Tools",
            f"{sum(totals['tool_calls'].values())} call(s): {_tool_breakdown(totals['tool_calls'])}"
            + (f" ({totals['tool_errors']} failed)" if totals["tool_errors"] else ""),
        ),
        ("Tokens", format_tokens(totals["usage"])),
    ]
    retries = [f"{n} {kind.replace('_', ' ')}" for kind, n in totals["retries"].items() if n]
    if retries:
        rows.append(("Retries", ", ".join(retries)))
    if result.get("final_text"):
        rows.append(("Answer", one_line(result["final_text"], 500)))
    if result.get("error"):
        rows.append(("Error", f"[red]{escape(result['error'])}[/]"))
    rows.append(("Folder", str(session.path)))
    for label, value in rows:
        overview.add_row(label, value if label == "Error" else escape(value))
    console.print(overview)

    if not steps:
        console.print("[dim]No steps were logged.[/]")
        _print_report_hint(console, session)
        return
    table = Table(title="Steps", title_justify="left")
    table.add_column("Step", justify="right")
    table.add_column("At", justify="right", no_wrap=True)
    table.add_column("Model", justify="right", no_wrap=True)
    table.add_column("Actions", justify="right", no_wrap=True)
    table.add_column("Tokens", justify="right")
    table.add_column("Calls")
    table.add_column("Reasoning", overflow="ellipsis", no_wrap=True, max_width=70)
    for step in steps:
        results = {r.get("call_id"): r for r in step.get("results") or []}
        calls = []
        for c in step.get("calls") or []:
            r = results.get(c.get("id"), {})
            name = escape(str(c.get("name")))
            calls.append(f"[red]{name} (failed)[/]" if r.get("is_error") else name)
        note = escape(one_line(_note(step), 70))
        if step.get("error") or step.get("stopped"):
            note = f"[red]{escape(step.get('error') or step.get('stopped'))}[/] {note}"
        table.add_row(
            format_number(step.get("step")),
            session.offset(step),
            format_seconds(step.get("model_s")),
            format_seconds(step.get("actions_s")),
            format_number((step.get("usage") or {}).get("total_tokens")),
            ", ".join(calls) or "[dim]answer[/]",
            note,
        )
    console.print(table)
    _print_report_hint(console, session)


def _print_report_hint(console: Console, session: Session) -> None:
    report = session.path / REPORT_FILE
    if report.exists():
        console.print(report_link(report), soft_wrap=True)
    else:
        console.print(f"[dim]A page with every step and screenshot: python runs.py {escape(session.id)} --html[/]")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None, console: Optional[Console] = None) -> int:
    parser = argparse.ArgumentParser(description="List and inspect the agent's saved sessions.")
    parser.add_argument(
        "session",
        nargs="?",
        help="A # from the list (1 = newest), 'latest', or a session id (enough of its start or end).",
    )
    parser.add_argument(
        "--html",
        action="store_true",
        help="(Re)build the session's report.html (steps, tool calls, screenshots) and open it.",
    )
    parser.add_argument("--no-open", dest="open", action="store_false", help="With --html: don't open the browser.")
    parser.add_argument("--limit", type=int, default=20, help="Sessions to list (0 = all).")
    parser.add_argument("--dir", default=str(RUNS_DIR), help="Where sessions are saved.")
    args = parser.parse_args(argv)
    console = console or Console()

    root = pathlib.Path(args.dir)
    sessions = list_sessions(root)
    if args.session is None and not args.html:
        show_list(console, sessions, root, args.limit)
        return 0
    try:
        session = find_session(sessions, args.session or "latest")
    except LookupError as e:
        console.print(f"[red]{escape(str(e))}[/]")
        return 1
    if args.html:
        path = write_report(session)
        console.print(report_link(path), soft_wrap=True)
        if args.open:
            webbrowser.open(path.as_uri())
        return 0
    show_session(console, session)
    return 0


if __name__ == "__main__":
    sys.exit(main())

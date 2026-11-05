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
"""Session logs: what each run of the agent leaves behind, and how to read it.

Every run is a session with its own folder, runs/<session id>/:

    run.json          what ran: task, model, settings (written at the start)
    trajectory.jsonl  one line per step: reasoning, tool calls and results,
                      timing, tokens, retries
    step_*.png        the screenshot after each action
    result.json       how it ended: status, answer, totals (written at the end)
    report.html       the session as a page to read in a browser (report.py,
                      written at the end)

A session id is the local start time plus a random suffix, for example
20261105-141500-a3f9c1. It sorts by time, says when the run happened, and
stays unique when runs start in the same second (two terminals, Docker and
local, parallel CI jobs) without a shared counter.

`python runs.py` lists and shows sessions.
"""
import dataclasses
import datetime
import json
import pathlib
import secrets
import sys
import time
from typing import Any, Optional

RUNS_DIR = pathlib.Path(__file__).resolve().parent / "runs"

RUN_FILE = "run.json"
STEPS_FILE = "trajectory.jsonl"
RESULT_FILE = "result.json"
REPORT_FILE = "report.html"

TOKEN_LABELS = {
    "total_tokens": "total",
    "total_input_tokens": "input",
    "total_output_tokens": "output",
    "total_thought_tokens": "thought",
    "total_cached_tokens": "cached",
}


def new_session_id(now: Optional[datetime.datetime] = None) -> str:
    now = now or datetime.datetime.now()
    return f"{now:%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"


def timestamp(seconds: Optional[float] = None) -> str:
    """ISO 8601 local time with its UTC offset, e.g. 2026-11-05T14:15:00.123+05:30."""
    moment = datetime.datetime.fromtimestamp(time.time() if seconds is None else seconds)
    return moment.astimezone().isoformat(timespec="milliseconds")


def parse_timestamp(value: Any) -> Optional[datetime.datetime]:
    if isinstance(value, (int, float)):
        return datetime.datetime.fromtimestamp(value).astimezone()
    if isinstance(value, str):
        try:
            return datetime.datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def format_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "-"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, secs = divmod(int(round(seconds)), 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def format_tokens(usage: dict[str, Any]) -> str:
    """'14,257 total (input 12,345, output 678, thought 1,234)'."""
    parts = [
        f"{label} {usage[key]:,}"
        for key, label in TOKEN_LABELS.items()
        if key != "total_tokens" and isinstance(usage.get(key), (int, float))
    ]
    total = usage.get("total_tokens")
    if not isinstance(total, (int, float)):
        return ", ".join(parts) or "-"
    return f"{total:,} total" + (f" ({', '.join(parts)})" if parts else "")


def format_number(value: Any) -> str:
    return f"{value:,}" if isinstance(value, (int, float)) else "-"


def format_seconds(value: Any) -> str:
    return f"{value:.1f}s" if isinstance(value, (int, float)) else "-"


def one_line(text: Optional[str], limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------


class SessionLog:
    """Writes one session's folder.

    A log must never stop a run: if the folder cannot be created or written
    (read-only mount, full disk, ...), it warns once and turns itself off.
    """

    def __init__(self, root: "str | pathlib.Path", session_id: str):
        self.session_id = session_id
        self.path: Optional[pathlib.Path] = None
        root = pathlib.Path(root)
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self._turn_off(e)
            return
        for _ in range(5):
            candidate = root / self.session_id
            try:
                candidate.mkdir()
            except FileExistsError:  # same second and same suffix: pick another
                self.session_id = new_session_id()
                continue
            except OSError as e:
                self._turn_off(e)
                return
            self.path = candidate
            return

    @property
    def enabled(self) -> bool:
        return self.path is not None

    def write_json(self, name: str, data: Any) -> None:
        self._write(name, json.dumps(data, indent=2, default=str))

    def append_step(self, record: dict[str, Any]) -> None:
        self._write(STEPS_FILE, json.dumps(record, default=str) + "\n", append=True)

    def write_bytes(self, name: str, data: bytes) -> Optional[str]:
        """Saves a file (a screenshot) and returns its name, or None if it wasn't saved."""
        if self.path is None:
            return None
        try:
            (self.path / name).write_bytes(data)
        except OSError as e:
            self._turn_off(e)
            return None
        return name

    def _write(self, name: str, text: str, append: bool = False) -> None:
        if self.path is None:
            return
        try:
            with open(self.path / name, "a" if append else "w", encoding="utf-8") as f:
                f.write(text)
        except OSError as e:
            self._turn_off(e)

    def _turn_off(self, error: BaseException) -> None:
        print(f"Warning: session log turned off: {error}", file=sys.stderr)
        self.path = None


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------


def _read_json(path: pathlib.Path) -> Optional[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # missing, or being written right now
        return None
    return data if isinstance(data, dict) else None


@dataclasses.dataclass
class Session:
    id: str
    path: pathlib.Path
    run: dict[str, Any]  # run.json; {} for logs written before it existed
    result: Optional[dict[str, Any]]  # None while running or if the process was killed

    @property
    def status(self) -> str:
        return (self.result or {}).get("status") or "unfinished"

    @property
    def query(self) -> Optional[str]:
        return self.run.get("query")

    @property
    def started_at(self) -> Optional[datetime.datetime]:
        for source in (self.run, self.result or {}):
            moment = parse_timestamp(source.get("started_at"))
            if moment:
                return moment
        try:  # older logs: the folder name is the start time
            return datetime.datetime.strptime(self.id[:15], "%Y%m%d-%H%M%S").astimezone()
        except ValueError:
            return None

    def steps(self) -> list[dict[str, Any]]:
        records = []
        try:
            lines = (self.path / STEPS_FILE).read_text(encoding="utf-8").splitlines()
        except OSError:
            return records
        for line in lines:
            try:
                record = json.loads(line)
            except ValueError:  # a half-written last line
                continue
            if isinstance(record, dict):
                records.append(record)
        return records

    def offset(self, step: dict[str, Any]) -> str:
        """When `step` started, counted from the start of the session: '+1:05'."""
        start, at = self.started_at, parse_timestamp(step.get("started_at"))
        if not (start and at):
            return "-"
        minutes, seconds = divmod(int(max(0.0, (at - start).total_seconds())), 60)
        return f"+{minutes}:{seconds:02d}"

    def totals(self, steps: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
        """Run totals from result.json, or added up from the steps when the run
        didn't finish (or was logged by an older version)."""
        result = self.result or {}
        if "tool_calls" in result:
            return {
                "steps": result.get("steps"),
                "duration_s": result.get("duration_s"),
                "model_s": result.get("model_s"),
                "actions_s": result.get("actions_s"),
                "usage": result.get("usage") or {},
                "tool_calls": result.get("tool_calls") or {},
                "tool_errors": result.get("tool_errors") or 0,
                "retries": result.get("retries") or {},
            }
        steps = self.steps() if steps is None else steps
        usage: dict[str, int] = {}
        tool_calls: dict[str, int] = {}
        retries = {"empty_response": 0, "network": 0}
        tool_errors = 0
        model_s = actions_s = 0.0
        for step in steps:
            for key, value in (step.get("usage") or {}).items():
                if isinstance(value, (int, float)):
                    usage[key] = usage.get(key, 0) + value
            for c in step.get("calls") or []:
                tool_calls[c.get("name")] = tool_calls.get(c.get("name"), 0) + 1
            tool_errors += sum(1 for r in step.get("results") or [] if r.get("is_error"))
            model_s += step.get("model_s") or 0.0
            actions_s += step.get("actions_s") or 0.0
            step_retries = step.get("retries") or {}
            retries["empty_response"] += step_retries.get("empty_response") or 0
            retries["network"] += len(step_retries.get("network") or [])
        duration = None
        start, end = self.started_at, steps and parse_timestamp(steps[-1].get("time"))
        if start and end:
            duration = max(0.0, (end - start).total_seconds())
        return {
            "steps": result.get("steps", len(steps)),
            "duration_s": duration,
            "model_s": model_s if steps and "model_s" in steps[0] else None,
            "actions_s": actions_s if steps and "actions_s" in steps[0] else None,
            "usage": result.get("usage") or usage,
            "tool_calls": tool_calls,
            "tool_errors": tool_errors,
            "retries": retries,
        }


def load_session(path: "str | pathlib.Path") -> Session:
    """The session saved in the folder `path`."""
    path = pathlib.Path(path)
    return Session(
        id=path.name,
        path=path,
        run=_read_json(path / RUN_FILE) or {},
        result=_read_json(path / RESULT_FILE),
    )


def list_sessions(root: "str | pathlib.Path" = RUNS_DIR) -> list[Session]:
    """All sessions under `root`, newest first."""
    root = pathlib.Path(root)
    if not root.is_dir():
        return []
    sessions = [
        load_session(path)
        for path in root.iterdir()
        if path.is_dir()
        and any((path / name).exists() for name in (RUN_FILE, STEPS_FILE, RESULT_FILE))
    ]
    # Ids start with the start time, so name order is time order.
    return sorted(sessions, key=lambda s: s.id, reverse=True)

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
"""Session logs (telemetry.py) and the runs.py viewer."""
import datetime
import io
import json
from html.parser import HTMLParser

import pytest
from rich.console import Console

import agent as agent_module
import report
import runs
from backends import ModelTurn
from telemetry import (
    SessionLog,
    format_duration,
    format_tokens,
    list_sessions,
    load_session,
    new_session_id,
)
from tests.test_agent import call, run_with

OLD = "20261105-141500-aaaaaa"
NEW = "20261106-090000-bbbbbb"


def write_session(root, session_id, *, query="Find the weather", steps=(), result=None, run=True):
    path = root / session_id
    path.mkdir(parents=True)
    if run:
        (path / "run.json").write_text(
            json.dumps(
                {
                    "session_id": session_id,
                    "query": query,
                    "model": "gemini-3.8-flash",
                    "api": "interactions",
                    "environment": "browser",
                    "started_at": "2026-11-05T14:15:00.000+00:00",
                }
            )
        )
    if steps:
        (path / "trajectory.jsonl").write_text("".join(json.dumps(s) + "\n" for s in steps))
    if result is not None:
        (path / "result.json").write_text(json.dumps(result))
    return path


START = datetime.datetime(2026, 11, 5, 14, 15, 0, tzinfo=datetime.timezone.utc)


def step(n, *calls, failed=(), usage=None, **extra):
    return {
        "step": n,
        "time": START.timestamp() + n + 1,  # each step takes a second
        "started_at": f"2026-11-05T14:15:{n:02d}.000+00:00",
        "model_s": 1.5,
        "actions_s": 0.5,
        "calls": [{"id": f"c{n}{i}", "name": name, "args": {"intent": f"do {name}"}} for i, name in enumerate(calls)],
        "results": [
            {"call_id": f"c{n}{i}", "name": name, "payload": {}, "is_error": name in failed, "duration_s": 0.5}
            for i, name in enumerate(calls)
        ],
        "usage": usage or {"total_tokens": 100},
        "retries": {"empty_response": 0, "network": []},
        **extra,
    }


def output(*argv) -> tuple[int, str]:
    console = Console(file=io.StringIO(), width=200)
    code = runs.main(list(argv), console=console)
    return code, console.file.getvalue()


def report_element_contexts(markup):
    class Elements(HTMLParser):
        def __init__(self):
            super().__init__()
            self.elements = []
            self.stack = []

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            self.elements.append((tag, attrs, tuple(self.stack)))
            if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
                self.stack.append((tag, attrs))

        def handle_endtag(self, tag):
            for index in range(len(self.stack) - 1, -1, -1):
                if self.stack[index][0] == tag:
                    del self.stack[index:]
                    break

    parser = Elements()
    parser.feed(markup)
    return parser.elements


def report_elements(markup):
    return [(tag, attrs) for tag, attrs, _ in report_element_contexts(markup)]


def report_links(markup):
    return [attrs for tag, attrs in report_elements(markup) if tag == "a"]


# ---------------------------------------------------------------------------
# telemetry.py
# ---------------------------------------------------------------------------


def test_session_id_sorts_by_time_and_is_unique():
    now = datetime.datetime(2026, 11, 5, 14, 15, 0)
    a, b = new_session_id(now), new_session_id(now)
    assert a.startswith("20261105-141500-") and len(a) == 22 and a != b
    assert new_session_id(now + datetime.timedelta(seconds=1)) > a


def test_session_id_collision_picks_another(tmp_path):
    (tmp_path / "taken").mkdir()
    log = SessionLog(tmp_path, "taken")
    assert log.enabled and log.session_id != "taken" and log.path.name == log.session_id


@pytest.mark.parametrize(
    "seconds,text", [(None, "-"), (4.26, "4.3s"), (92, "1m 32s"), (3725, "1h 02m")]
)
def test_format_duration(seconds, text):
    assert format_duration(seconds) == text


def test_format_tokens():
    usage = {"total_tokens": 14257, "total_input_tokens": 12345, "total_output_tokens": 678}
    assert format_tokens(usage) == "14,257 total (input 12,345, output 678)"
    assert format_tokens({"total_tokens": 5}) == "5 total"
    assert format_tokens({}) == "-"


def test_list_sessions_newest_first(tmp_path):
    write_session(tmp_path, OLD, result={"status": "completed"})
    write_session(tmp_path, NEW, result={"status": "error"})
    (tmp_path / "not-a-session").mkdir()
    (tmp_path / ".gitkeep").write_text("")
    assert [s.id for s in list_sessions(tmp_path)] == [NEW, OLD]
    assert list_sessions(tmp_path / "missing") == []


def test_unfinished_session_totals_come_from_its_steps(tmp_path):
    path = write_session(
        tmp_path,
        OLD,
        steps=[step(1, "navigate", "click", failed=("click",)), step(2, "type", usage={"total_tokens": 50})],
    )
    with open(path / "trajectory.jsonl", "a") as f:
        f.write('{"step": 3, "half-writ')  # killed mid-write
    (session,) = list_sessions(tmp_path)
    assert session.status == "unfinished"
    totals = session.totals()
    assert totals["steps"] == 2
    assert totals["tool_calls"] == {"navigate": 1, "click": 1, "type": 1}
    assert totals["tool_errors"] == 1
    assert totals["usage"] == {"total_tokens": 150}
    assert totals["model_s"] == 3.0 and totals["actions_s"] == 1.0
    assert totals["duration_s"] == pytest.approx(3.0)  # start to the end of step 2


def test_logs_from_before_run_json_still_list(tmp_path):
    path = tmp_path / "20261101-080000"
    path.mkdir()
    (path / "result.json").write_text(json.dumps({"status": "completed", "steps": 1, "usage": {"total_tokens": 9}}))
    (path / "trajectory.jsonl").write_text(json.dumps({"step": 1, "time": 1.0, "calls": [{"name": "click"}]}) + "\n")
    (session,) = list_sessions(tmp_path)
    assert session.started_at.strftime("%Y-%m-%d %H:%M") == "2026-11-01 08:00"
    totals = session.totals()
    assert totals["usage"] == {"total_tokens": 9} and totals["tool_calls"] == {"click": 1}
    assert totals["model_s"] is None
    code, out = output(session.id, "--dir", str(tmp_path))
    assert code == 0 and "click" in out
    html = report.render_report(session)  # no run.json, no timing fields: still a page
    assert "Step 1" in html and "Click" in html and 'id="settings-title"' not in html


# ---------------------------------------------------------------------------
# runs.py
# ---------------------------------------------------------------------------


class TestFindSession:
    def setup_method(self):
        self.sessions = [runs.Session(id=i, path=None, run={}, result=None) for i in (NEW, OLD)]

    @pytest.mark.parametrize(
        "selector,expected",
        [("latest", NEW), ("1", NEW), ("2", OLD), (OLD, OLD), ("20261105", OLD), ("bbbbbb", NEW)],
    )
    def test_selectors(self, selector, expected):
        assert runs.find_session(self.sessions, selector).id == expected

    def test_ambiguous_and_missing(self):
        with pytest.raises(LookupError, match="matches 2 sessions"):
            runs.find_session(self.sessions, "2026")
        with pytest.raises(LookupError, match="No session matches"):
            runs.find_session(self.sessions, "zzz")
        with pytest.raises(LookupError, match="no sessions"):
            runs.find_session([], "latest")


def test_list_view(tmp_path):
    write_session(
        tmp_path,
        OLD,
        query="Find the weather in Paris",
        result={
            "status": "completed",
            "steps": 3,
            "duration_s": 92,
            "usage": {"total_tokens": 4321},
            "tool_calls": {"click": 2, "type": 1},
            "tool_errors": 1,
        },
    )
    code, out = output("--dir", str(tmp_path))
    assert code == 0
    for text in (OLD, "completed", "1m 32s", "4,321", "3 (1 failed)", "Find the weather in Paris"):
        assert text in out


def test_empty_list(tmp_path):
    code, out = output("--dir", str(tmp_path))
    assert code == 0 and "No sessions" in out


def test_unknown_session(tmp_path):
    write_session(tmp_path, OLD)
    code, out = output("nope", "--dir", str(tmp_path))
    assert code == 1 and "No session matches" in out


def test_agent_session_end_to_end(tmp_path):
    computer_turns = [
        ModelTurn(text="Opening", calls=[call("navigate", url="https://example.com", intent="Open the site")]),
        ModelTurn(text="It is sunny.", usage={"total_tokens": 42}),
    ]
    agent, backend, result = run_with(computer_turns, log_dir=str(tmp_path))

    code, out = output("--dir", str(tmp_path))
    assert code == 0 and result.session_id in out and "test query" in out

    code, out = output("latest", "--dir", str(tmp_path))
    assert code == 0
    for text in (result.session_id, "navigate", "Open the site", "It is sunny.", "waiting for Gemini"):
        assert text in out

    page = tmp_path / result.session_id / "report.html"
    assert result.report_path == str(page)  # written when the run ended
    page.unlink()
    code, out = output("latest", "--dir", str(tmp_path))
    assert "--html" in out and "Report:" not in out  # no page: how to make one

    code, out = output("1", "--html", "--no-open", "--dir", str(tmp_path))
    html = page.read_text(encoding="utf-8")
    assert code == 0 and f"Report: {page.resolve().as_uri()}" in out
    assert "Open the site" in html and "It is sunny." in html
    assert 'src="step_001_0_navigate.png" alt="' in html  # no size: not a real PNG

    code, out = output("latest", "--dir", str(tmp_path))
    assert f"Report: {page.resolve().as_uri()}" in out


def test_html_report_escapes_page_content(tmp_path):
    path = write_session(
        tmp_path,
        OLD,
        query="<script>alert(1)</script>",
        steps=[step(1, "click"), step(2, "click")],
    )
    (path / "step_001_0_click.png").write_bytes(b"png")
    lines = (path / "trajectory.jsonl").read_text().splitlines()
    first, second = json.loads(lines[0]), json.loads(lines[1])
    first["results"][0]["screenshot"] = "step_001_0_click.png"
    second["results"][0]["screenshot"] = "../../secret.png"  # never rendered
    second["text"] = '"><img src=x onerror=alert(1)>'
    (path / "trajectory.jsonl").write_text(json.dumps(first) + "\n" + json.dumps(second) + "\n")

    html = report.render_report(list_sessions(tmp_path)[0])
    assert "<script>alert" not in html and "&lt;script&gt;" in html
    assert "onerror=alert(1)>" not in html
    assert 'src="step_001_0_click.png"' in html and "secret.png" not in html
    assert html.count("<script") == 1 and "script-src 'nonce-" in html  # only the page's own


# ---------------------------------------------------------------------------
# report.py
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "result,steps,expected",
    [
        ({"status": "completed", "final_text": "It is sunny."}, [step(1, "click")], ("Completed", "It is sunny.")),
        (
            {"status": "error", "error": "BadRequestError: blocked"},
            [step(1, "click")],
            ("Failed", "The run failed after step 1", "BadRequestError: blocked"),
        ),
        (
            {"status": "terminated", "error": "Interrupted by user."},
            [step(1, "click"), step(2, "type", stopped="terminated")],
            (
                "Stopped",
                "The run was stopped at step 2",
                "Ctrl+C was pressed, or the process got SIGTERM",
                "The run was stopped in this step.",
                'href="#step-2"',
            ),
        ),
        (
            {"status": "terminated", "error": "Action was not confirmed."},  # a safety prompt answered No
            [step(1, "click", stopped="terminated")],
            ("The run was stopped at step 1", "safety confirmation and it was declined"),
        ),
        (
            {"status": "max_steps", "error": "Stopped after 1 steps (--max_steps)."},
            [step(1, "click"), {**step(2, "click"), "results": [], "stopped": "max_steps"}],
            (
                "Step limit reached",
                "The run hit its step limit at step 2",
                "Raise --max_steps",
                "The step limit was reached: the actions below were not run.",
                "Not run: the run stopped before this action.",
            ),
        ),
        ({"status": "error", "error": "No API key"}, [], ("The run failed before the first step", "No steps were logged")),
        ({"status": "completed"}, [], ("The agent finished the task", "It finished without a final message.")),
        (None, [step(1, "click")], ("Unfinished", "No result yet")),
        (
            {"status": "paused", "error": "Waiting for input"},  # a status this page doesn't know
            [step(1, "click")],
            ("Paused", "The run ended with status “paused” after step 1", "Waiting for input", "tone-idle"),
        ),
    ],
)
def test_report_says_how_the_run_ended(tmp_path, result, steps, expected):
    write_session(tmp_path, OLD, steps=steps, result=result)
    html = report.render_report(load_session(tmp_path / OLD))
    for text in expected:
        assert text in html


def test_report_without_run_json_or_task(tmp_path):
    write_session(tmp_path, OLD, steps=[step(1, "click")], result={"status": "completed"}, run=False)
    html = report.render_report(load_session(tmp_path / OLD))
    assert f"Session {OLD}" in html and "Step 1" in html and "Settings" not in html
    write_session(tmp_path, NEW, query="", result={"status": "error"})
    html = report.render_report(load_session(tmp_path / NEW))
    assert f"Session {NEW}" in html and "No steps were logged" in html


def test_report_shows_clock_times_from_the_log(tmp_path):
    # Logged at UTC+00:00; the page shows those clock times, whatever zone it is opened in.
    write_session(
        tmp_path,
        OLD,
        steps=[step(1, "navigate", "click")],
        result={
            "status": "completed",
            "started_at": "2026-11-05T14:15:00.000+00:00",
            "ended_at": "2026-11-05T14:17:05.500+00:00",
            "duration_s": 125.5,
            "model_s": 100.0,
            "actions_s": 20.0,
            "tool_calls": {"navigate": 1, "click": 1},
        },
    )
    html = report.render_report(load_session(tmp_path / OLD)).replace(" ", " ")  # figures don't wrap inside
    assert "Nov 5, 2026 at 14:15:00 → 14:17:05" in html  # the run
    split = '<li class=""><span>{}</span><span class="kpi-num">{}</span></li>'.format
    assert split("Gemini", "1m 40s") in html and split("Actions", "20.0s") in html and split("Other", "5.5s") in html
    assert "14:15:01 → 14:15:02 (+0:01)" in html  # the step: started_at → time, offset from the start
    # Logs without each action's times: the step's end, minus the actions that ran after it.
    assert "14:15:01 → 14:15:01 (+0:01) · took 0.5s" in html  # navigate: 14:15:01.0 → 14:15:01.5
    assert "14:15:01 → 14:15:02 (+0:01) · took 0.5s" in html  # click: 14:15:01.5 → 14:15:02.0
    assert "timebar" not in html and "nav-bar" not in html  # colours with no words: gone


def test_report_uses_the_logged_action_times(tmp_path):
    # A safety prompt waited 40s between the two actions: only logged times show that.
    record = step(1, "navigate", "click")
    record["time"] = START.timestamp() + 45
    record["results"][0].update(started_at="2026-11-05T14:15:02.000+00:00", ended_at="2026-11-05T14:15:03.000+00:00")
    record["results"][1].update(started_at="2026-11-05T14:15:43.000+00:00", ended_at="2026-11-05T14:15:45.000+00:00")
    write_session(tmp_path, OLD, steps=[record], result={"status": "completed", "tool_calls": {}})
    html = report.render_report(load_session(tmp_path / OLD))
    assert "14:15:02 → 14:15:03 (+0:02) · took 0.5s" in html
    assert "14:15:43 → 14:15:45 (+0:43) · took 0.5s" in html


def test_report_puts_results_in_words(tmp_path):
    steps = [step(1, "navigate", "click", "yield_to_user", "lookup", "type", "custom", failed=("click",))]
    results = steps[0]["results"]
    results[0]["payload"] = {"url": "https://example.com/a", "safety_acknowledgement": True}
    results[1]["payload"] = {"error": "Timeout 30000ms exceeded", "url": "https://example.com/a"}
    results[2]["payload"] = {"user_response": "123456", "url": "javascript:alert(1)"}
    results[3]["payload"] = {"result": [1, 2]}
    results[4]["payload"] = {"active_window": "Notepad"}
    results[5]["payload"] = {"user_response": None, "error": "No interactive user is available."}
    steps[0]["calls"].append({"id": "c17", "name": "wait", "args": {}})  # no result: not run
    steps[0]["calls"][0]["args"]["url"] = "https://example.com/a"
    write_session(tmp_path, OLD, steps=steps, result={"status": "completed"})
    html = report.render_report(load_session(tmp_path / OLD))
    assert "Sent back to Gemini" in html and "Raw call and result (JSON)" in html
    row = '<div class="kv-row line-{}"><dt>{}</dt><dd>{}</dd></div>'.format
    link = '<a href="https://example.com/a" rel="noopener noreferrer" target="_blank">https://example.com/a</a>'
    assert row("url", "Page after", link) in html
    assert row("text", "Safety confirmation", "you (or --safety auto-confirm) approved this action.") in html
    assert row("error", "Failed", "Timeout 30000ms exceeded") in html
    assert row("text", "Your reply", "123456") in html
    assert row("url", "Page after", "javascript:alert(1)") in html  # not a link
    assert row("text", "result", "[1, 2]") in html and row("text", "Active window", "Notepad") in html
    assert row("error", "Error reported", "No interactive user is available.") in html and "No reply from you." in html
    assert '<div class="kv-row kv-code"><dt>url</dt>' in html  # the arguments, one row each
    assert "Not run: the run stopped before this action." in html and html.count('class="action') >= 7
    assert "&quot;safety_acknowledgement&quot;: true" in html  # the raw JSON is still there, escaped


def test_report_reserves_room_for_screenshots(tmp_path):
    path = write_session(tmp_path, OLD, steps=[step(1, "click")])
    png = report.PNG_SIGNATURE + b"\x00\x00\x00\rIHDR" + (1440).to_bytes(4, "big") + (900).to_bytes(4, "big")
    (path / "step_001_0_click.png").write_bytes(png)
    record = json.loads((path / "trajectory.jsonl").read_text())
    record["results"][0]["screenshot"] = "step_001_0_click.png"
    (path / "trajectory.jsonl").write_text(json.dumps(record) + "\n")
    html = report.render_report(load_session(path))
    assert html.count('src="step_001_0_click.png" width="1440" height="900"') == 2  # preview + step
    assert 'class="shot shot-full" href="step_001_0_click.png"' in html


def test_report_preview_uses_latest_valid_screenshot(tmp_path):
    records = [step(n, "click") for n in range(1, 6)]
    captures = ["first.png", "last & capture.png", "missing.png", "../outside.png", "page.html"]
    for record, capture in zip(records, captures):
        record["results"][0]["screenshot"] = capture
    path = write_session(tmp_path, OLD, steps=records)
    for name in ("first.png", "last & capture.png", "page.html"):
        (path / name).write_bytes(b"capture")
    (tmp_path / "outside.png").write_bytes(b"outside session")

    markup = report.render_report(load_session(path))
    links = report_links(markup)
    preview = next(link for link in links if link.get("id") == "preview-shot")
    elements = report_element_contexts(markup)
    options = [attrs for tag, attrs, ancestors in elements if tag == "option"
               and any(parent == "select" and props.get("id") == "capture-select" for parent, props in ancestors)]
    assert preview["href"] == "last%20%26%20capture.png"
    assert [option["value"] for option in options] == ["first.png", "last%20%26%20capture.png"]
    assert [option["value"] for option in options if "selected" in option] == [preview["href"]]


def test_report_preview_escapes_captions(tmp_path):
    hostile = 'https://example.com/?q="><img src=x onerror=alert(1)>&tag=1'
    record = step(1, "navigate")
    record["calls"][0]["args"].pop("intent")
    record["calls"][0]["args"]["url"] = hostile
    record["results"][0]["screenshot"] = "capture.png"
    path = write_session(tmp_path, OLD, steps=[record])
    (path / "capture.png").write_bytes(b"capture")

    markup = report.render_report(load_session(path))
    options = [attrs for tag, attrs in report_elements(markup) if tag == "option"]
    assert len(options) == 1 and hostile in options[0]["data-caption"]
    assert options[0]["value"] == "capture.png" and "onerror" not in options[0]
    assert '<img src=x onerror=alert(1)>' not in markup


@pytest.mark.parametrize("records", [(), (step(1, "click"),)])
def test_report_has_no_gallery_without_captures(tmp_path, records):
    path = write_session(tmp_path, OLD, steps=records)
    markup = report.render_report(load_session(path))
    links = report_links(markup)
    assert not any(link.get("id") == "preview-shot" for link in links)
    assert not any(attrs.get("id") == "capture-select" for _, attrs in report_elements(markup))


def test_report_many_captures_have_one_overview_image(tmp_path):
    records = [step(n, "click") for n in range(1, 51)]
    for n, record in enumerate(records, 1):
        record["results"][0]["screenshot"] = f"capture_{n:03d}.png"
    path = write_session(tmp_path, OLD, steps=records)
    for record in records:
        (path / record["results"][0]["screenshot"]).write_bytes(b"capture")

    elements = report_element_contexts(report.render_report(load_session(path)))
    options = [attrs for tag, attrs, ancestors in elements if tag == "option"
               and any(parent == "select" and props.get("id") == "capture-select" for parent, props in ancestors)]
    images = [(attrs, ancestors) for tag, attrs, ancestors in elements if tag == "img" and attrs.get("src")]
    overview_images = [attrs for attrs, ancestors in images if any(props.get("id") == "overview" for _, props in ancestors)]
    assert len(options) == 50 and len(images) == 51  # one preview plus each step; viewer starts without a source
    assert len(overview_images) == 1 and overview_images[0]["src"] == "capture_050.png"
    assert [option["value"] for option in options if "selected" in option] == ["capture_050.png"]


def test_report_metric_breakdowns_are_visible_without_disclosures(tmp_path):
    usage = {"total_input_tokens": 1_000, "total_cached_tokens": 400, "total_output_tokens": 20,
             "total_thought_tokens": 10, "total_tokens": 1_030}
    path = write_session(tmp_path, OLD, steps=[step(1, "click", usage=usage)], result={"status": "completed"})
    elements = report_element_contexts(report.render_report(load_session(path)))
    rows = [ancestors for _, attrs, ancestors in elements if "kpi-rows" in attrs.get("class", "").split()]
    assert len(rows) == 5
    assert not any(tag == "details" or "hidden" in attrs for ancestors in rows for tag, attrs in ancestors)


@pytest.mark.parametrize("records", [(), (step(1, "click"),)])
def test_report_panels_are_linked_and_readable_without_javascript(tmp_path, records):
    hostile = '\"><button id="injected" onclick="alert(1)">Task</button>'
    path = write_session(tmp_path, OLD, query=hostile, steps=records)
    elements = report_elements(report.render_report(load_session(path)))
    for name in ("overview", "activity", "settings"):
        tabs = [(tag, attrs) for tag, attrs in elements if attrs.get("id") == f"tab-{name}"]
        panels = [(tag, attrs) for tag, attrs in elements if attrs.get("id") == name]
        assert len(tabs) == 1 and len(panels) == 1
        tag, tab = tabs[0]
        assert tag == "a" and tab["href"] == f"#{name}" and tab["aria-controls"] == name
        _, panel = panels[0]
        assert "report-panel" in panel.get("class", "").split() and "hidden" not in panel
    assert not any(attrs.get("id") == "injected" for _, attrs in elements)
    assert not any(key.lower().startswith("on") for _, attrs in elements for key in attrs)


def test_report_has_no_settings_panel_without_run_json(tmp_path):
    path = write_session(tmp_path, OLD, steps=[step(1, "click")], run=False)
    elements = report_elements(report.render_report(load_session(path)))
    ids = [attrs.get("id") for _, attrs in elements]
    assert "settings" not in ids and "tab-settings" not in ids
    assert all(name in ids for name in ("overview", "activity", "tab-overview", "tab-activity"))


def test_report_estimates_the_cost(tmp_path):
    usage = {"total_input_tokens": 100_000, "total_cached_tokens": 40_000, "total_output_tokens": 2_000, "total_tokens": 102_000}
    steps = [step(1, "click", usage=usage), step(2, "click", usage=usage)]
    write_session(tmp_path, OLD, steps=steps, result={"status": "completed"})
    html = report.render_report(load_session(tmp_path / OLD))
    # Each step: 60,000 × $0.75 + 40,000 × $0.075 + 2,000 × $3.75 per 1M = $0.0555; the run: two of them.
    assert '<dd class="kpi-value">$0.1110</dd>' in html and "Estimated cost" in html
    assert '<span>Cached input</span><span class="kpi-num">$0.0060</span>' in html
    assert '<span>Output + thinking</span><span class="kpi-num">$0.0150</span>' in html
    assert '<dd class="sstat-value">$0.0555</dd>' in html  # each step's own cost
    assert "for gemini-3.8-flash." in html and "billed as" not in html


def test_report_without_a_price(tmp_path):
    path = write_session(tmp_path, OLD, steps=[step(1, "click")], result={"status": "completed"})
    run = json.loads((path / "run.json").read_text())
    (path / "run.json").write_text(json.dumps({**run, "model": "my-tuned-model"}))
    html = report.render_report(load_session(path))
    assert "No price on file for my-tuned-model." in html and '<dd class="kpi-value">-</dd>' in html
    write_session(tmp_path, NEW, steps=[step(1, "click")], result={"status": "completed"}, run=False)
    assert "this session has no run.json" in report.render_report(load_session(tmp_path / NEW))


@pytest.mark.parametrize(
    "name,args,label",
    [
        ("navigate", {"url": "https://example.com"}, "Go to https://example.com"),
        ("type_text_at", {"text": "hello", "press_enter": True}, "Type “hello” and press Enter"),
        ("key_combination", {"keys": ["Control", "c"]}, "Press Control+c"),
        ("scroll_document", {"direction": "down"}, "Scroll the page down"),
        ("lookup_order", {}, "Lookup order"),
        ("type", {}, "Type “”"),
    ],
)
def test_actions_are_described_in_words(name, args, label):
    assert report.describe_action(name, args)[1] == label


def test_report_is_written_when_a_run_fails(tmp_path):
    agent, backend, result = run_with(
        [ModelTurn(calls=[call("wait", seconds=0)]), ModelTurn(error="Interaction failed")],
        log_dir=str(tmp_path),
    )
    page = tmp_path / result.session_id / "report.html"
    assert result.status == "error" and result.report_path == str(page)
    assert "The run failed at step 2" in page.read_text(encoding="utf-8")
    saved = json.loads((tmp_path / result.session_id / "result.json").read_text())
    assert saved["report_path"] == str(page)


def test_a_broken_report_does_not_stop_the_run(tmp_path, monkeypatch, capsys):
    def broken(session):
        raise OSError("disk full")

    monkeypatch.setattr(agent_module, "write_report", broken)
    agent, backend, result = run_with([ModelTurn(text="ok")], log_dir=str(tmp_path), verbose=True)
    captured = capsys.readouterr()
    assert result.ok and result.report_path is None
    assert "could not write the session report: disk full" in captured.err
    assert "Report" not in captured.out and result.session_id in captured.out
    saved = json.loads((tmp_path / result.session_id / "result.json").read_text())
    assert saved["status"] == "completed" and saved["report_path"] is None


def test_report_link(tmp_path, monkeypatch):
    page = tmp_path / "s" / "report.html"
    link = report.report_link(page)
    assert link.plain == f"Report: {page.resolve().as_uri()}"
    assert any(span.style.link == page.resolve().as_uri() for span in link.spans)  # clickable

    # In Docker the file:// URL would point into the container; runs/ is mounted.
    monkeypatch.setattr(report, "_in_container", lambda: True)
    monkeypatch.setattr(report, "RUNS_DIR", tmp_path.resolve())
    assert report.report_link(page).plain.startswith("Report: runs/s/report.html")
    assert report.report_link(tmp_path.parent / "x.html").plain.startswith("Report: file://")

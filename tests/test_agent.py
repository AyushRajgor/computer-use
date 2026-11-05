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
import datetime
import json
import os
from unittest.mock import MagicMock

import pytest
from google.genai import types

from agent import BrowserAgent, ComputerUseAgent, create_client
from backends import ModelTurn, ToolCall
from computers import EnvState, UnsupportedActionError
from custom_functions import YIELD_TO_USER, CustomFunction
from tests.conftest import interaction


ACTION_METHODS = (
    "current_state click_at double_click_at triple_click_at middle_click_at right_click_at "
    "mouse_down mouse_up hover_at type_text type_text_at scroll_at scroll_document "
    "key_combination press_key key_down key_up drag_and_drop wait wait_5_seconds "
    "take_screenshot open_web_browser navigate go_back go_forward search"
).split()


def mock_computer() -> MagicMock:
    computer = MagicMock()
    computer.screen_size.return_value = (1000, 1000)
    computer.environment = "browser"
    computer.live_view_url.return_value = None
    state = EnvState(screenshot=b"png", url="https://start")
    for method in ACTION_METHODS:
        getattr(computer, method).return_value = state
    return computer


def make_agent(computer=None, **kwargs) -> ComputerUseAgent:
    if computer is None:
        computer = mock_computer()
    kwargs.setdefault("client", MagicMock())
    kwargs.setdefault("verbose", False)
    return ComputerUseAgent(browser_computer=computer, query="test query", **kwargs)


def call(name, **args):
    return ToolCall(name=name, args=args, id=f"id-{name}")


# ---------------------------------------------------------------------------
# action dispatch
# ---------------------------------------------------------------------------


class TestHandleAction:
    def setup_method(self):
        self.agent = make_agent()
        self.c = self.agent._browser_computer

    def test_backwards_compatible_alias(self):
        assert BrowserAgent is ComputerUseAgent

    def test_click_is_denormalized(self):
        self.c.screen_size.return_value = (1440, 900)
        self.agent.handle_action(call("click", x=500, y=500, intent="click it"))
        self.c.click_at.assert_called_once_with(x=720, y=450)

    def test_coordinates_are_clamped(self):
        self.agent.handle_action(call("click", x=1000, y=-5))
        self.c.click_at.assert_called_once_with(x=999, y=0)

    @pytest.mark.parametrize(
        "name,method",
        [
            ("double_click", "double_click_at"),
            ("triple_click", "triple_click_at"),
            ("middle_click", "middle_click_at"),
            ("right_click", "right_click_at"),
            ("mouse_down", "mouse_down"),
            ("mouse_up", "mouse_up"),
            ("move", "hover_at"),
            ("hover_at", "hover_at"),
            ("click_at", "click_at"),
        ],
    )
    def test_mouse_actions(self, name, method):
        self.agent.handle_action(call(name, x=100, y=200))
        getattr(self.c, method).assert_called_once_with(x=100, y=200)

    def test_type_without_coordinates(self):
        self.agent.handle_action(call("type", text="hello", press_enter=True))
        self.c.type_text.assert_called_once_with(text="hello", press_enter=True)

    def test_type_with_coordinates(self):
        self.agent.handle_action(call("type", text="hi", x=100, y=200, clear_before_typing=True))
        self.c.type_text.assert_called_once_with(
            text="hi", press_enter=False, x=100, y=200, clear_before_typing=True
        )

    def test_legacy_type_text_at(self):
        self.agent.handle_action(call("type_text_at", x=100, y=200, text="hello"))
        self.c.type_text_at.assert_called_once_with(
            x=100, y=200, text="hello", press_enter=False, clear_before_typing=True
        )

    def test_drag_and_drop_current_schema(self):
        self.agent.handle_action(call("drag_and_drop", start_x=1, start_y=2, end_x=300, end_y=400))
        self.c.drag_and_drop.assert_called_once_with(x=1, y=2, destination_x=300, destination_y=400)

    def test_drag_and_drop_legacy_schema(self):
        self.agent.handle_action(
            call("drag_and_drop", x=1, y=2, destination_x=300, destination_y=400)
        )
        self.c.drag_and_drop.assert_called_once_with(x=1, y=2, destination_x=300, destination_y=400)

    def test_scroll_magnitude_in_pixels(self):
        self.agent.handle_action(
            call("scroll", x=500, y=500, direction="down", magnitude_in_pixels=250)
        )
        self.c.scroll_at.assert_called_once_with(x=500, y=500, direction="down", magnitude=250)

    def test_scroll_default_magnitude(self):
        self.agent.handle_action(call("scroll", x=500, y=500, direction="up"))
        self.c.scroll_at.assert_called_once_with(x=500, y=500, direction="up", magnitude=300)

    def test_legacy_scroll_at_is_normalized(self):
        self.c.screen_size.return_value = (1000, 500)
        self.agent.handle_action(call("scroll_at", x=500, y=500, direction="down", magnitude=500))
        self.c.scroll_at.assert_called_once_with(x=500, y=250, direction="down", magnitude=250)

    def test_scroll_rejects_bad_direction(self):
        with pytest.raises(ValueError):
            self.agent.handle_action(call("scroll", x=1, y=1, direction="diagonal"))

    def test_hotkey_list_and_legacy_string(self):
        self.agent.handle_action(call("hotkey", keys=["Control", "c"]))
        self.c.key_combination.assert_called_with(["Control", "c"])
        self.agent.handle_action(call("key_combination", keys="control+shift+t"))
        self.c.key_combination.assert_called_with(["control", "shift", "t"])

    def test_keys(self):
        self.agent.handle_action(call("press_key", key="Enter"))
        self.c.press_key.assert_called_once_with("Enter")
        self.agent.handle_action(call("key_down", key="Shift"))
        self.c.key_down.assert_called_once_with("Shift")
        self.agent.handle_action(call("key_up", key="Shift"))
        self.c.key_up.assert_called_once_with("Shift")

    def test_wait_is_capped(self):
        self.agent.handle_action(call("wait", seconds=600))
        self.c.wait.assert_called_once_with(60)
        self.agent.handle_action(call("wait_5_seconds"))
        self.c.wait_5_seconds.assert_called_once()

    def test_navigation(self):
        self.agent.handle_action(call("navigate", url="https://example.com"))
        self.c.navigate.assert_called_once_with("https://example.com")
        self.agent.handle_action(call("go_back"))
        self.c.go_back.assert_called_once()
        self.agent.handle_action(call("go_forward"))
        self.c.go_forward.assert_called_once()
        self.agent.handle_action(call("search"))
        self.c.search.assert_called_once()

    def test_misc(self):
        self.agent.handle_action(call("take_screenshot"))
        self.c.take_screenshot.assert_called_once()
        self.agent.handle_action(call("open_web_browser"))
        self.c.open_web_browser.assert_called_once()
        self.agent.handle_action(call("scroll_document", direction="down"))
        self.c.scroll_document.assert_called_once_with("down")

    def test_accepts_genai_function_call(self):
        self.agent.handle_action(types.FunctionCall(name="navigate", args={"url": "https://x"}))
        self.c.navigate.assert_called_once_with("https://x")

    def test_mobile_only_action_is_unsupported(self):
        with pytest.raises(UnsupportedActionError):
            self.agent.handle_action(call("open_app", app_name="Maps"))

    def test_unknown_function(self):
        with pytest.raises(ValueError):
            self.agent.handle_action(call("unknown_function"))

    def test_custom_function(self):
        fn = CustomFunction(
            name="multiply",
            description="Multiplies",
            parameters={"type": "object", "properties": {}},
            handler=lambda a: {"result": a["x"] * a["y"]},
        )
        agent = make_agent(custom_functions=[fn])
        assert agent.handle_action(call("multiply", x=2, y=3)) == {"result": 6}


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------


class TestConfig:
    def test_prompt_injection_detection_defaults_by_model(self):
        assert make_agent(model_name="gemini-3.8-flash")._backend_config.enable_prompt_injection_detection
        assert not make_agent(
            model_name="gemini-3-flash-preview"
        )._backend_config.enable_prompt_injection_detection
        assert make_agent(model_name="my-tuned-model")._backend_config.enable_prompt_injection_detection is None
        assert not make_agent(
            enable_prompt_injection_detection=False
        )._backend_config.enable_prompt_injection_detection

    def test_thinking_level_validation(self):
        with pytest.raises(ValueError):
            make_agent(model_name="gemini-3.8-flash", thinking_level="minimal")
        with pytest.raises(ValueError):
            make_agent(thinking_level="extreme")
        make_agent(model_name="gemini-3.8-flash", thinking_level="low")

    def test_unknown_safety_policy(self):
        with pytest.raises(ValueError):
            make_agent(disabled_safety_policies=["everything"])

    def test_environment_comes_from_computer(self):
        computer = MagicMock()
        computer.environment = "desktop"
        agent = make_agent(computer=computer)
        assert agent._backend_config.environment == "desktop"

    def test_unknown_api(self):
        with pytest.raises(ValueError):
            make_agent(api="nope")

    def test_create_client_developer_api(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "abc")
        assert create_client() is not None

    def test_create_client_enterprise(self, monkeypatch):
        monkeypatch.setenv("USE_VERTEXAI", "true")
        monkeypatch.setenv("VERTEXAI_PROJECT", "my-project")
        captured = {}
        import agent as agent_module

        monkeypatch.setattr(agent_module.genai, "Client", lambda **kw: captured.update(kw) or "client")
        assert create_client() == "client"
        assert captured == {"enterprise": True, "project": "my-project", "location": "global"}


# ---------------------------------------------------------------------------
# safety decisions
# ---------------------------------------------------------------------------


class TestSafety:
    SAFETY = {"decision": "require_confirmation", "explanation": "Buying something"}

    def test_auto_confirm(self):
        assert make_agent(safety_mode="auto-confirm")._get_safety_confirmation(self.SAFETY) == "CONTINUE"

    def test_deny(self):
        assert make_agent(safety_mode="deny")._get_safety_confirmation(self.SAFETY) == "TERMINATE"

    def test_blocked_decision_terminates(self):
        agent = make_agent(safety_mode="auto-confirm")
        assert agent._get_safety_confirmation({"decision": "blocked"}) == "TERMINATE"

    def test_regular_decision_continues(self):
        assert make_agent(safety_mode="deny")._get_safety_confirmation({"decision": "regular"}) == "CONTINUE"

    def test_ask_without_tty_denies(self, monkeypatch):
        monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: False))
        assert make_agent()._get_safety_confirmation(self.SAFETY) == "TERMINATE"

    @pytest.mark.parametrize("answer,expected", [("yes", "CONTINUE"), ("n", "TERMINATE")])
    def test_ask_with_tty(self, monkeypatch, answer, expected):
        monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: True))
        agent = make_agent(input_fn=lambda prompt: answer)
        assert agent._get_safety_confirmation(self.SAFETY) == expected


# ---------------------------------------------------------------------------
# the loop (with a scripted backend)
# ---------------------------------------------------------------------------


class ScriptedBackend:
    def __init__(self, turns):
        self.turns = list(turns)
        self.sent = []
        self.started = None
        self.retries = 0

    def start(self, query, screenshot):
        self.started = (query, screenshot)
        return self.turns.pop(0)

    def send_results(self, results):
        self.sent.append(list(results))
        return self.turns.pop(0)

    def retry(self):
        self.retries += 1
        return self.turns.pop(0)


def run_with(turns, **kwargs):
    agent = make_agent(**kwargs)
    backend = ScriptedBackend(turns)
    agent._backend = backend
    return agent, backend, agent.run()


class TestLoop:
    def test_completes_without_calls(self):
        agent, backend, result = run_with([ModelTurn(text="Done.", usage={"total_tokens": 5})])
        assert result.status == "completed" and result.ok
        assert result.final_text == "Done." == agent.final_reasoning
        assert backend.started == ("test query", b"png")
        assert result.usage == {"total_tokens": 5}

    def test_executes_calls_and_returns_state(self):
        agent, backend, result = run_with(
            [
                ModelTurn(calls=[call("navigate", url="https://example.com", intent="go")]),
                ModelTurn(text="Finished"),
            ]
        )
        assert result.status == "completed"
        (res,) = backend.sent[0]
        assert res.payload == {"url": "https://start"}
        assert res.screenshot == b"png"
        assert not res.is_error

    def test_action_errors_are_reported_to_model(self):
        computer = mock_computer()
        computer.navigate.side_effect = PermissionError("blocked by policy")
        agent, backend, result = run_with(
            [ModelTurn(calls=[call("navigate", url="https://evil")]), ModelTurn(text="ok")],
            computer=computer,
        )
        (res,) = backend.sent[0]
        assert res.is_error and "blocked by policy" in res.payload["error"]
        assert result.ok

    def test_safety_acknowledgement_is_sent(self):
        safety = {"decision": "require_confirmation", "explanation": "x"}
        agent, backend, result = run_with(
            [ModelTurn(calls=[call("click", x=1, y=1, safety_decision=safety)]), ModelTurn(text="ok")],
            safety_mode="auto-confirm",
        )
        assert backend.sent[0][0].payload["safety_acknowledgement"] is True

    def test_denied_safety_terminates(self):
        safety = {"decision": "require_confirmation", "explanation": "x"}
        agent, backend, result = run_with(
            [ModelTurn(calls=[call("click", x=1, y=1, safety_decision=safety)])],
            safety_mode="deny",
        )
        assert result.status == "terminated"
        assert backend.sent == []

    def test_max_steps(self):
        turns = [ModelTurn(calls=[call("wait", seconds=0)]) for _ in range(5)]
        agent, backend, result = run_with(turns, max_steps=2)
        assert result.status == "max_steps"
        assert len(backend.sent) == 2

    def test_error_turn(self):
        agent, backend, result = run_with([ModelTurn(error="Interaction failed")])
        assert result.status == "error" and result.error == "Interaction failed"

    def test_empty_turn_is_retried(self):
        agent, backend, result = run_with(
            [ModelTurn(retryable_empty=True), ModelTurn(text="recovered")]
        )
        assert backend.retries == 1
        assert result.final_text == "recovered"

    def test_exhausted_empty_retries_is_an_error(self):
        turns = [ModelTurn(retryable_empty=True) for _ in range(5)]
        agent, backend, result = run_with(turns)
        assert result.status == "error" and not result.ok
        assert "empty or malformed" in result.error

    def test_exceptions_become_error_result(self):
        agent = make_agent()

        class Boom(ScriptedBackend):
            def start(self, query, screenshot):
                raise RuntimeError("network down")

        agent._backend = Boom([])
        result = agent.run()
        assert result.status == "error" and "network down" in result.error

    def test_custom_function_with_screenshot(self):
        fn = CustomFunction(
            name="ask",
            description="",
            parameters={"type": "object"},
            handler=lambda a: {"answer": 42},
            returns_screenshot=True,
        )
        agent, backend, result = run_with(
            [ModelTurn(calls=[call("ask")]), ModelTurn(text="ok")], custom_functions=[fn]
        )
        res = backend.sent[0][0]
        assert res.payload == {"answer": 42, "url": "https://start"}
        assert res.screenshot == b"png"


# ---------------------------------------------------------------------------
# telemetry and session logs
# ---------------------------------------------------------------------------


def read_steps(root, result):
    lines = (root / result.session_id / "trajectory.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines]


class TestTelemetry:
    def test_session_log(self, tmp_path):
        agent, backend, result = run_with(
            [
                ModelTurn(calls=[call("take_screenshot", intent="look")], usage={"total_tokens": 7}),
                ModelTurn(text="done", usage={"total_tokens": 3}),
            ],
            log_dir=str(tmp_path),
        )
        assert os.listdir(tmp_path) == [result.session_id]
        run_dir = tmp_path / result.session_id
        assert result.log_dir == str(run_dir)

        run = json.loads((run_dir / "run.json").read_text())
        assert run["session_id"] == result.session_id and run["query"] == "test query"
        assert run["model"] and run["api"] == "interactions" and run["started_at"]

        first, last = read_steps(tmp_path, result)
        assert first["step"] == 1 and first["calls"][0]["name"] == "take_screenshot"
        assert first["usage"] == {"total_tokens": 7}
        assert first["model_s"] >= 0 and first["actions_s"] >= 0 and first["started_at"]
        (res,) = first["results"]
        assert res["duration_s"] >= 0 and not res["is_error"]
        began, ended = (datetime.datetime.fromisoformat(res[k]) for k in ("started_at", "ended_at"))
        assert began <= ended and began >= datetime.datetime.fromisoformat(first["started_at"])
        assert (run_dir / res["screenshot"]).read_bytes() == b"png"
        assert last["step"] == 2 and last["text"] == "done" and "stopped" not in last

        saved = json.loads((run_dir / "result.json").read_text())
        assert saved["status"] == "completed" and saved["session_id"] == result.session_id
        assert saved["tool_calls"] == {"take_screenshot": 1} and saved["tool_errors"] == 0
        assert saved["usage"] == {"total_tokens": 10}
        assert saved["duration_s"] >= 0 and saved["ended_at"]

    def test_numbers_are_returned_without_a_log(self):
        computer = mock_computer()
        computer.navigate.side_effect = PermissionError("blocked")
        agent, backend, result = run_with(
            [
                ModelTurn(calls=[call("navigate", url="https://x"), call("wait", seconds=0)]),
                ModelTurn(text="ok"),
            ],
            computer=computer,
        )
        assert result.log_dir is None and result.session_id
        assert result.tool_calls == {"navigate": 1, "wait": 1} and result.tool_errors == 1
        assert result.duration_s >= result.actions_s >= 0 and result.model_s >= 0
        assert result.started_at and result.ended_at
        assert result.retries == {"empty_response": 0, "network": 0}
        assert all(r.duration_s is not None for r in backend.sent[0])

    def test_error_turn_is_logged(self, tmp_path):
        agent, backend, result = run_with(
            [ModelTurn(calls=[call("wait", seconds=0)]), ModelTurn(error="Interaction failed")],
            log_dir=str(tmp_path),
        )
        steps = read_steps(tmp_path, result)
        assert [s["step"] for s in steps] == [1, 2]
        assert steps[1]["stopped"] == "error" and steps[1]["error"] == "Interaction failed"

    def test_max_steps_turn_is_logged_without_results(self, tmp_path):
        turns = [ModelTurn(calls=[call("wait", seconds=0)]) for _ in range(3)]
        agent, backend, result = run_with(turns, max_steps=1, log_dir=str(tmp_path))
        steps = read_steps(tmp_path, result)
        assert steps[-1]["stopped"] == "max_steps"
        assert steps[-1]["calls"] and steps[-1]["results"] == []

    def test_interrupted_step_keeps_the_finished_calls(self, tmp_path):
        computer = mock_computer()
        computer.click_at.side_effect = KeyboardInterrupt
        agent, backend, result = run_with(
            [ModelTurn(calls=[call("wait", seconds=0), call("click", x=1, y=1)])],
            computer=computer,
            log_dir=str(tmp_path),
        )
        assert result.status == "terminated"
        (step,) = read_steps(tmp_path, result)
        assert step["stopped"] == "terminated"
        assert [r["name"] for r in step["results"]] == ["wait"]
        assert result.tool_calls == {"wait": 1, "click": 1}
        assert json.loads((tmp_path / result.session_id / "result.json").read_text())["status"] == "terminated"

    def test_retries_are_counted_per_step(self, tmp_path):
        agent = make_agent(log_dir=str(tmp_path))

        class Flaky(ScriptedBackend):
            def start(self, query, screenshot):
                agent._on_retry(1, 0.0, TimeoutError("slow"))  # what call_with_retries reports
                return super().start(query, screenshot)

        agent._backend = Flaky(
            [ModelTurn(retryable_empty=True, usage={"total_tokens": 3}), ModelTurn(text="ok", usage={"total_tokens": 5})]
        )
        result = agent.run()
        assert result.usage == {"total_tokens": 8}
        assert result.retries == {"empty_response": 1, "network": 1}
        (step,) = read_steps(tmp_path, result)
        assert step["usage"] == {"total_tokens": 8}
        assert step["retries"]["empty_response"] == 1
        assert step["retries"]["network"][0]["error"] == "TimeoutError: slow"

    def test_unwritable_log_folder_does_not_stop_the_run(self, tmp_path, capsys):
        not_a_folder = tmp_path / "file"
        not_a_folder.write_text("x")
        agent, backend, result = run_with([ModelTurn(text="ok")], log_dir=str(not_a_folder))
        assert result.ok and result.log_dir is None
        assert "session log turned off" in capsys.readouterr().err

    def test_summary(self, tmp_path, capsys):
        agent, backend, result = run_with(
            [
                ModelTurn(calls=[call("wait", seconds=0)], usage={"total_tokens": 1200, "total_input_tokens": 1000}),
                ModelTurn(text="ok"),
            ],
            verbose=True,
            log_dir=str(tmp_path),
        )
        out = capsys.readouterr().out
        assert "waiting for Gemini" in out and "Tools: 1 call(s) (wait 1)" in out
        assert "Tokens: 1,200 total (input 1,000)" in out
        report = tmp_path / result.session_id / "report.html"
        assert result.report_path == str(report) and report.is_file()
        assert f"Report: {report.resolve().as_uri()}" in out
        assert f"python runs.py {result.session_id}" in out


def test_yield_to_user_without_tty(monkeypatch):
    monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: False))
    assert YIELD_TO_USER({"reason": "2FA"})["user_response"] is None


def test_full_loop_against_fake_interactions_api(fake_gemini):
    """End to end through the real SDK: agent -> InteractionsBackend -> HTTP."""
    fake_gemini.responses = [
        interaction(calls=[("click", {"x": 500, "y": 500, "intent": "Click"})], id_="i1"),
        interaction(text="All done", id_="i2"),
    ]
    agent = make_agent(client=fake_gemini.client())
    result = agent.run()
    assert result.ok and result.final_text == "All done"
    agent._browser_computer.click_at.assert_called_once_with(x=500, y=500)
    second = fake_gemini.requests[1]
    assert second["previous_interaction_id"] == "i1"
    (fr,) = second["input"]
    assert fr["type"] == "function_result" and fr["call_id"] == "i1-call-0"
    assert result.usage["total_tokens"] == 20


def test_session_log_records_interaction_ids(fake_gemini, tmp_path):
    fake_gemini.responses = [
        interaction(calls=[("click", {"x": 500, "y": 500})], id_="i1"),
        interaction(text="All done", id_="i2"),
    ]
    result = make_agent(client=fake_gemini.client(), log_dir=str(tmp_path)).run()
    steps = read_steps(tmp_path, result)
    assert [s["response_id"] for s in steps] == ["i1", "i2"]
    assert [s["usage"]["total_tokens"] for s in steps] == [10, 10]

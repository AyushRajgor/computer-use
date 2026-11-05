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
from unittest.mock import patch

import pytest

import main
from agent import AgentResult
from models import DEFAULT_MODEL


def parse(*argv):
    return main.build_parser().parse_args(["--query", "q", *argv])


def ok_result():
    return AgentResult(status="completed", final_text="done", steps=1, usage={})


class TestParser:
    def test_defaults(self):
        args = parse()
        assert args.env == "playwright"
        assert args.model == DEFAULT_MODEL == "gemini-3.8-flash"
        assert args.api == "interactions"
        assert args.history == "server"
        assert args.screen_size == main.PLAYWRIGHT_SCREEN_SIZE == (1440, 900)
        assert args.safety == "ask"
        assert args.max_steps == 100
        assert args.prompt_injection_detection is None
        assert args.headless is None
        assert not args.hitl

    def test_backwards_compatible_flags(self):
        args = parse("--initial_url", "https://a", "--highlight_mouse")
        assert args.initial_url == "https://a" and args.highlight_mouse
        args = parse("--initial-url", "https://b", "--highlight-mouse")
        assert args.initial_url == "https://b" and args.highlight_mouse

    def test_env_var_defaults(self, monkeypatch):
        monkeypatch.setenv("COMPUTER_USE_ENV", "browserbase")
        monkeypatch.setenv("GEMINI_MODEL", "gemini-4-argon")
        monkeypatch.setenv("SCREEN_SIZE", "1280x800")
        monkeypatch.setenv("PLAYWRIGHT_HEADLESS", "false")
        monkeypatch.setenv("GEMINI_DISABLED_SAFETY_POLICIES", "data_modification,account_creation")
        monkeypatch.setenv("SAFETY_CONFIRMATION", "deny")
        monkeypatch.setenv("MAX_STEPS", "7")
        args = parse()
        assert args.env == "browserbase"
        assert args.model == "gemini-4-argon"
        assert args.screen_size == (1280, 800)
        assert args.headless is False
        assert args.disable_safety_policy == ["data_modification", "account_creation"]
        assert args.safety == "deny"
        assert args.max_steps == 7

    def test_flags_override_env(self, monkeypatch):
        monkeypatch.setenv("PLAYWRIGHT_HEADLESS", "false")
        assert parse("--headless").headless is True
        assert parse("--no-prompt_injection_detection").prompt_injection_detection is False

    def test_bad_screen_size(self):
        with pytest.raises(SystemExit):
            parse("--screen_size", "big")

    def test_repeatable_lists(self):
        args = parse("--exclude_action", "drag_and_drop", "--exclude_action", "hotkey")
        assert args.exclude_action == ["drag_and_drop", "hotkey"]


class TestBuildComputer:
    @pytest.mark.parametrize(
        "env,cls",
        [
            ("playwright", "PlaywrightComputer"),
            ("cdp", "CDPComputer"),
            ("browserbase", "BrowserbaseComputer"),
            ("browser-use", "BrowserUseComputer"),
            ("desktop", "DesktopComputer"),
        ],
    )
    def test_selects_class(self, env, cls):
        with patch(f"main.{cls}") as mock_cls:
            main.build_computer(parse("--env", env, "--cdp_url", "http://x:9222"))
        mock_cls.assert_called_once()

    def test_playwright_options(self):
        with patch("main.PlaywrightComputer") as mock_cls:
            main.build_computer(
                parse(
                    "--headless",
                    "--browser_channel",
                    "chrome",
                    "--allowed_domains",
                    "example.com, google.com",
                    "--tab_mode",
                    "follow",
                )
            )
        kwargs = mock_cls.call_args.kwargs
        assert kwargs["headless"] is True
        assert kwargs["browser_channel"] == "chrome"
        assert kwargs["allowed_domains"] == ["example.com", "google.com"]
        assert kwargs["tab_mode"] == "follow"
        assert kwargs["screen_size"] == (1440, 900)


class TestMain:
    @patch("main.PlaywrightComputer")
    @patch("main.ComputerUseAgent")
    def test_main_playwright(self, mock_agent, mock_computer):
        mock_agent.return_value.agent_loop.return_value = ok_result()
        assert main.main(["--query", "test", "--initial_url", "test_url", "--highlight_mouse"]) == 0
        kwargs = mock_computer.call_args.kwargs
        assert kwargs["initial_url"] == "test_url" and kwargs["highlight_mouse"] is True
        agent_kwargs = mock_agent.call_args.kwargs
        assert agent_kwargs["query"] == "test"
        assert agent_kwargs["model_name"] == DEFAULT_MODEL
        assert agent_kwargs["custom_functions"] == []
        mock_agent.return_value.agent_loop.assert_called_once()

    @patch("main.BrowserbaseComputer")
    @patch("main.ComputerUseAgent")
    def test_main_browserbase_failure_exit_code(self, mock_agent, mock_computer):
        mock_agent.return_value.agent_loop.return_value = AgentResult(
            status="error", final_text=None, steps=1, usage={}, error="x"
        )
        assert main.main(["--query", "test", "--env", "browserbase"]) == 1
        mock_computer.assert_called_once()

    @patch("main.PlaywrightComputer")
    @patch("main.ComputerUseAgent")
    def test_hitl_and_system_instruction_file(self, mock_agent, mock_computer, tmp_path):
        mock_agent.return_value.agent_loop.return_value = ok_result()
        f = tmp_path / "si.txt"
        f.write_text("Never buy anything.")
        main.main(["--query", "q", "--hitl", "--system_instruction_file", str(f)])
        kwargs = mock_agent.call_args.kwargs
        assert [fn.name for fn in kwargs["custom_functions"]] == ["yield_to_user"]
        assert kwargs["system_instruction"] == "Never buy anything."

    def test_env_alias(self):
        with patch("main.run_gemini", return_value=0) as run:
            main.main(["--query", "q", "--env", "browseruse"])
        assert run.call_args.args[0].env == "browser-use"

    def test_setup_errors_exit_cleanly(self, capsys):
        with patch("main.BrowserbaseComputer", side_effect=ValueError("BROWSERBASE_API_KEY is not set.")):
            assert main.main(["--query", "q", "--env", "browserbase"]) == 2
        assert "BROWSERBASE_API_KEY" in capsys.readouterr().err

    def test_sigterm_is_handled_like_ctrl_c(self):
        import signal

        previous = signal.getsignal(signal.SIGTERM)
        try:
            main._install_sigterm_handler()
            with pytest.raises(KeyboardInterrupt):
                signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        finally:
            signal.signal(signal.SIGTERM, previous)

    def test_unknown_env(self):
        with pytest.raises(SystemExit):
            main.main(["--query", "q", "--env", "mars"])

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
import json
import os
import shutil
import socket
import struct
import subprocess
import time
import urllib.request
from unittest.mock import MagicMock, patch

import pytest

from computers import (
    BrowserbaseComputer,
    BrowserUseComputer,
    CDPComputer,
    DesktopComputer,
    PlaywrightComputer,
)
from computers.desktop.desktop import XDOTOOL_KEY_MAP
from computers.playwright.playwright import DomainPolicy


def png_size(data: bytes) -> tuple[int, int]:
    return struct.unpack(">II", data[16:24])


class TestDomainPolicy:
    def test_blocklist(self):
        p = DomainPolicy(blocked=["example.com"])
        assert not p.is_allowed("https://example.com/a")
        assert not p.is_allowed("https://sub.example.com/a")
        assert p.is_allowed("https://notexample.com")
        assert p.is_allowed("about:blank")

    def test_non_http_schemes(self):
        active = DomainPolicy(allowed=["example.com"])
        assert not active.is_allowed("file:///etc/passwd")
        assert not active.is_allowed("chrome://settings")
        assert active.is_allowed("about:blank") and active.is_allowed("data:text/html,x")
        assert DomainPolicy().is_allowed("file:///tmp/x")  # no policy: anything goes

    def test_allowlist(self):
        p = DomainPolicy(allowed=["*.google.com", "wikipedia.org"])
        assert p.is_allowed("https://www.google.com")
        assert p.is_allowed("https://en.wikipedia.org/wiki/x")
        assert not p.is_allowed("https://evil.com")
        assert not DomainPolicy().active


class TestBrowserbaseParams:
    def test_defaults(self):
        params = BrowserbaseComputer(screen_size=(1280, 720)).session_create_params()
        assert params["browser_settings"] == {"viewport": {"width": 1280, "height": 720}}
        assert "project_id" not in params  # optional: inferred from the API key
        assert params["user_metadata"] == {"source": "gemini-computer-use"}

    def test_env_configuration(self, monkeypatch):
        for key, value in {
            "BROWSERBASE_PROJECT_ID": "proj",
            "BROWSERBASE_REGION": "eu-central-1",
            "BROWSERBASE_PROXY_COUNTRY": "de",
            "BROWSERBASE_PROXY_CITY": "Berlin",
            "BROWSERBASE_SOLVE_CAPTCHAS": "false",
            "BROWSERBASE_BLOCK_ADS": "true",
            "BROWSERBASE_RECORD_SESSION": "true",
            "BROWSERBASE_CONTEXT_ID": "ctx",
            "BROWSERBASE_PERSIST_CONTEXT": "true",
            "BROWSERBASE_KEEP_ALIVE": "true",
            "BROWSERBASE_SESSION_TIMEOUT": "900",
            "BROWSERBASE_EXTENSION_ID": "ext",
            "BROWSERBASE_OS": "windows",
        }.items():
            monkeypatch.setenv(key, value)
        params = BrowserbaseComputer(allowed_domains=["example.com"]).session_create_params()
        bs = params["browser_settings"]
        assert bs["solve_captchas"] is False and bs["block_ads"] is True and bs["record_session"] is True
        assert bs["context"] == {"id": "ctx", "persist": True}
        assert bs["os"] == "windows"
        assert bs["allowed_domains"] == ["example.com"]
        assert params["project_id"] == "proj" and params["region"] == "eu-central-1"
        assert params["proxies"] == [
            {"type": "browserbase", "geolocation": {"country": "DE", "city": "Berlin"}}
        ]
        assert params["keep_alive"] is True and params["api_timeout"] == 900
        assert params["extension_id"] == "ext"

    def test_verified_skips_viewport(self):
        params = BrowserbaseComputer(verified=True).session_create_params()
        assert "viewport" not in params["browser_settings"]
        assert params["browser_settings"]["verified"] is True

    def test_invalid_region(self):
        with pytest.raises(ValueError):
            BrowserbaseComputer(region="mars-1")

    def test_session_lifecycle(self):
        computer = BrowserbaseComputer(api_key="key")
        session = MagicMock(id="sess", connect_url="wss://connect")
        with patch("browserbase.Browserbase") as bb_cls:
            bb = bb_cls.return_value
            bb.sessions.create.return_value = session
            bb.sessions.debug.return_value = MagicMock(debugger_fullscreen_url="https://live")
            assert computer.start_session() == "wss://connect"
            assert computer.live_view_url() == "https://live"
            computer.stop_session()
            computer.stop_session()  # idempotent
        bb.sessions.update.assert_called_once_with("sess", status="REQUEST_RELEASE")

    def test_requires_api_key(self):
        with pytest.raises(ValueError):
            BrowserbaseComputer().start_session()


class TestBrowserUseParams:
    def test_defaults(self):
        params = BrowserUseComputer(screen_size=(1280, 720)).session_create_params()
        assert params == {
            "browser_screen_width": 1280,
            "browser_screen_height": 720,
            "metadata": {"source": "gemini-computer-use"},
        }

    def test_env_configuration(self, monkeypatch):
        monkeypatch.setenv("BROWSER_USE_PROXY_COUNTRY", "DE")
        monkeypatch.setenv("BROWSER_USE_PROFILE_ID", "prof")
        monkeypatch.setenv("BROWSER_USE_SESSION_TIMEOUT", "30")
        monkeypatch.setenv("BROWSER_USE_ENABLE_RECORDING", "true")
        monkeypatch.setenv("BROWSER_USE_EXTENSION_IDS", "a, b")
        monkeypatch.setenv("BROWSER_USE_CUSTOM_PROXY", "user:pw@proxy.local:8080")
        params = BrowserUseComputer().session_create_params()
        assert params["proxy_country_code"] == "de"
        assert params["profile_id"] == "prof"
        assert params["timeout"] == 30
        assert params["enable_recording"] is True
        assert params["extension_ids"] == ["a", "b"]
        proxy = params["custom_proxy"]
        dumped = proxy.model_dump(mode="json")
        assert (dumped["host"], dumped["port"], dumped["username"], dumped["password"]) == (
            "proxy.local",
            8080,
            "user",
            "pw",
        )

    def test_disable_proxy(self, monkeypatch):
        monkeypatch.setenv("BROWSER_USE_PROXY_COUNTRY", "none")
        assert BrowserUseComputer().session_create_params()["proxy_country_code"] is None

    def test_session_lifecycle(self):
        computer = BrowserUseComputer(api_key="key")
        session = MagicMock(id="b1", cdp_url="wss://cdp", live_url="https://live")
        with patch("browser_use_sdk.v4.BrowserUse") as client_cls:
            client = client_cls.return_value
            client.browsers.create.return_value = session
            assert computer.start_session() == "wss://cdp"
            assert computer.live_view_url() == "https://live"
            computer.stop_session()
            computer.stop_session()
        client.browsers.stop.assert_called_once_with("b1")

    def test_missing_cdp_url_stops_browser(self):
        computer = BrowserUseComputer(api_key="key")
        with patch("browser_use_sdk.v4.BrowserUse") as client_cls:
            client = client_cls.return_value
            client.browsers.create.return_value = MagicMock(id="b1", cdp_url=None)
            with pytest.raises(RuntimeError):
                computer.start_session()
        client.browsers.stop.assert_called_once_with("b1")


def _fake_cdp_browser(computer):
    """Wires a mocked Playwright into `computer`; returns (browser, existing tab, new tab)."""
    computer._playwright = MagicMock()
    browser = computer._playwright.chromium.connect_over_cdp.return_value
    context = MagicMock()
    existing, new_tab = MagicMock(), MagicMock()
    existing.evaluate.return_value = new_tab.evaluate.return_value = "Windows"
    context.pages = [existing]
    context.new_page.return_value = new_tab
    browser.contexts = [context]
    return browser, existing, new_tab


class TestCDP:
    def test_default_target_is_running_chrome(self):
        assert CDPComputer()._cdp_endpoint() == "chrome"
        assert CDPComputer("http://x:9222")._cdp_endpoint() == "http://x:9222"

    def test_running_browser_gets_its_own_tab(self):
        computer = CDPComputer("msedge")
        browser, existing, new_tab = _fake_cdp_browser(computer)
        computer._connect()
        assert computer._page is new_tab
        existing.set_viewport_size.assert_not_called()
        computer._disconnect()
        new_tab.close.assert_not_called()  # left open so the result stays visible
        browser.close.assert_called_once()  # disconnects; does not kill the browser

    def test_unreachable_running_browser_gives_actionable_error(self):
        computer = CDPComputer("chrome")
        computer._playwright = MagicMock()
        computer._playwright.chromium.connect_over_cdp.side_effect = Exception(
            "DevToolsActivePort file not found"
        )
        with pytest.raises(RuntimeError, match="chrome://inspect/#remote-debugging"):
            computer._connect()

    def test_browser_name_is_case_insensitive(self):
        computer = CDPComputer(" Chrome ")
        browser, _, new_tab = _fake_cdp_browser(computer)
        computer._connect()
        # Playwright only resolves the exact lowercase name.
        assert computer._playwright.chromium.connect_over_cdp.call_args.args[0] == "chrome"
        assert computer._page is new_tab

    def test_url_target_takes_over_first_tab(self):
        computer = CDPComputer("http://x:9222")
        _, existing, new_tab = _fake_cdp_browser(computer)
        computer._connect()
        assert computer._page is existing
        assert not computer._own_tab

    def test_ignores_tabs_the_agent_did_not_open(self):
        computer = CDPComputer("chrome")
        agent_tab, user_tab, popup = MagicMock(), MagicMock(), MagicMock()
        computer._page = agent_tab
        user_tab.opener.return_value = None
        popup.opener.return_value = agent_tab
        computer._own_tab = True
        assert computer._opened_by_agent(popup)
        assert not computer._opened_by_agent(user_tab)
        computer._own_tab = False
        assert computer._opened_by_agent(user_tab)


def test_desktop_key_mapping():
    assert DesktopComputer._keysym("Enter") == "Return"
    assert DesktopComputer._keysym("control") == "ctrl"
    assert DesktopComputer._keysym("C", in_combo=True) == "c"
    assert XDOTOOL_KEY_MAP["f5"] == "F5"


def test_desktop_requires_display(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    with pytest.raises(RuntimeError):
        DesktopComputer().__enter__()


# ---------------------------------------------------------------------------
# Integration tests (real Chromium / real X display). Skipped if unavailable.
# ---------------------------------------------------------------------------

CHROMIUM = os.environ.get("CHROMIUM_EXECUTABLE")  # optional override

TEST_PAGE = b"""<html><body style='margin:0'>
<input id=q style='position:fixed;left:100px;top:100px;width:300px;height:40px'>
<button style='position:fixed;left:500px;top:100px;width:200px;height:40px'
  onclick="location.hash='clicked-'+document.getElementById('q').value">Go</button>
<a href='/two' target='_blank' style='position:fixed;left:100px;top:300px;font-size:30px'>newtab</a>
<div style='height:3000px'></div></body></html>"""


@pytest.fixture
def browser(fake_gemini):
    fake_gemini.pages["/"] = TEST_PAGE
    fake_gemini.pages["/two"] = b"<html><body>two</body></html>"
    computer = PlaywrightComputer(
        screen_size=(1000, 700),
        initial_url=fake_gemini.url + "/",
        headless=True,
        executable_path=CHROMIUM,
        settle_time_s=0.1,
    )
    try:
        computer.__enter__()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Chromium not available: {e}")
    yield computer, fake_gemini.url
    computer.__exit__(None, None, None)


@pytest.mark.integration
def test_playwright_actions(browser):
    c, base = browser
    state = c.type_text("hello", x=250, y=120)
    assert png_size(state.screenshot) == (1000, 700) == c.screen_size()
    state = c.click_at(600, 120)
    assert state.url.endswith("#clicked-hello")
    state = c.scroll_at(500, 500, "down", 300)
    assert c._page.evaluate("scrollY") == 300
    state = c.click_at(150, 315)  # target=_blank -> opened in the same tab
    assert state.url == base + "/two"
    assert len(c._context.pages) == 1
    assert c.go_back().url.startswith(base + "/")
    c._domain_policy = DomainPolicy(blocked=["127.0.0.1"])
    with pytest.raises(PermissionError):
        c.navigate(base + "/two")


@pytest.mark.integration
def test_domain_policy_with_new_tab_and_redirect(fake_gemini):
    fake_gemini.pages["/"] = TEST_PAGE
    fake_gemini.pages["/two"] = b"<html><body>two</body></html>"
    port = fake_gemini.url.rsplit(":", 1)[1]
    computer = PlaywrightComputer(
        screen_size=(1000, 700),
        initial_url=fake_gemini.url + "/",
        headless=True,
        executable_path=CHROMIUM,
        settle_time_s=0.1,
        allowed_domains=["127.0.0.1"],
    )
    try:
        computer.__enter__()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Chromium not available: {e}")
    try:
        # target=_blank link with an active route: must not crash the action.
        state = computer.click_at(150, 315)
        assert state.url == fake_gemini.url + "/two"
        # A redirect from an allowed host to a disallowed one is caught after load.
        computer._page.evaluate(f"location.href = 'http://localhost:{port}/two'")
        state = computer.current_state()
        assert "Blocked" in computer._page.content()
        with pytest.raises(PermissionError):
            computer.navigate("file:///etc/hostname")
    finally:
        computer.__exit__(None, None, None)


def _chromium_executable():
    if CHROMIUM:
        return CHROMIUM
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            path = p.chromium.executable_path
        return path if os.path.exists(path) else None
    except Exception:  # noqa: BLE001
        return None


def _page_targets(endpoint: str) -> list[dict]:
    with urllib.request.urlopen(endpoint + "/json/list", timeout=5) as r:
        return [t for t in json.load(r) if t["type"] == "page"]


@pytest.mark.integration
def test_cdp_own_tab_leaves_user_tabs_alone(fake_gemini, tmp_path):
    """Attached to somebody's browser: own tab, user tabs untouched, tab kept on exit."""
    executable = _chromium_executable()
    if not executable:
        pytest.skip("Chromium not available")
    fake_gemini.pages["/"] = TEST_PAGE
    fake_gemini.pages["/two"] = b"<html><body>two</body></html>"
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    endpoint = f"http://127.0.0.1:{port}"
    args = [executable, "--headless=new", f"--remote-debugging-port={port}",
            f"--user-data-dir={tmp_path / 'profile'}", "--no-first-run",
            "--no-default-browser-check", "about:blank"]
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        args.insert(1, "--no-sandbox")  # Chrome refuses to start as root otherwise
    chrome = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                if _page_targets(endpoint):
                    break
            except OSError:
                pass
            time.sleep(0.1)
        computer = CDPComputer(
            endpoint,
            own_tab=True,
            screen_size=(1000, 700),
            initial_url=fake_gemini.url + "/",
            settle_time_s=0.1,
        )
        with computer as c:
            user_tab = next(p for p in c._context.pages if p is not c._page)
            assert user_tab.url == "about:blank"
            # The user opens a tab while the agent works: not adopted, not closed.
            mine = c._context.new_page()
            mine.goto(fake_gemini.url + "/two")
            assert c.current_state().url == fake_gemini.url + "/"
            assert not mine.is_closed()
            # The agent's own target=_blank link still opens in its tab.
            assert c.click_at(150, 315).url == fake_gemini.url + "/two"
            assert len(c._context.pages) == 3
        # Disconnected: the browser and all three tabs (agent's included) remain.
        assert len(_page_targets(endpoint)) == 3
    finally:
        chrome.kill()
        chrome.wait(timeout=10)


@pytest.mark.integration
def test_desktop_on_xvfb(tmp_path):
    if not (shutil.which("Xvfb") and shutil.which("xdotool") and shutil.which("xterm")):
        pytest.skip("Xvfb / xdotool / xterm not installed")
    display = ":93"
    xvfb = subprocess.Popen(["Xvfb", display, "-screen", "0", "1024x768x24", "-nolisten", "tcp"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.0)
    env = dict(os.environ, DISPLAY=display)
    term = subprocess.Popen(["xterm", "-geometry", "80x20+20+20"], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.0)
    out = tmp_path / "out.txt"
    try:
        with DesktopComputer(display=display, settle_time_s=0.1) as c:
            assert c.screen_size() == (1024, 768)
            state = c.current_state()
            assert png_size(state.screenshot) == (1024, 768)
            c.click_at(200, 100)
            c.type_text(f"echo desktop-ok > {out}", press_enter=True)
        for _ in range(20):
            if out.exists():
                break
            time.sleep(0.1)
        assert out.read_text().strip() == "desktop-ok"
    finally:
        term.kill()
        xvfb.kill()

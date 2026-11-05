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
"""A browser environment driven by Playwright.

`PlaywrightComputer` launches a local Chromium/Chrome. The remote browser
backends (`CDPComputer`, `BrowserbaseComputer`, `BrowserUseComputer`) subclass
it and only override how the browser is obtained (`_connect`) and released
(`_disconnect`); all actions are shared.
"""
import os
import struct
import sys
import time
from typing import Literal, Optional, Sequence
from urllib.parse import urlparse

import playwright.sync_api
import termcolor
from playwright.sync_api import sync_playwright

from ..computer import Computer, EnvState

# Map model / user-friendly key names to Playwright key names.
# See https://playwright.dev/docs/api/class-keyboard#keyboard-press
# Keys like 'a', 'b', '1', '$' are passed through unchanged.
PLAYWRIGHT_KEY_MAP = {
    "backspace": "Backspace",
    "tab": "Tab",
    "return": "Enter",
    "enter": "Enter",
    "shift": "Shift",
    "control": "ControlOrMeta",
    "ctrl": "ControlOrMeta",
    "alt": "Alt",
    "option": "Alt",
    "escape": "Escape",
    "esc": "Escape",
    "space": "Space",
    "spacebar": "Space",
    "pageup": "PageUp",
    "page_up": "PageUp",
    "pagedown": "PageDown",
    "page_down": "PageDown",
    "end": "End",
    "home": "Home",
    "left": "ArrowLeft",
    "arrowleft": "ArrowLeft",
    "up": "ArrowUp",
    "arrowup": "ArrowUp",
    "right": "ArrowRight",
    "arrowright": "ArrowRight",
    "down": "ArrowDown",
    "arrowdown": "ArrowDown",
    "insert": "Insert",
    "delete": "Delete",
    "del": "Delete",
    "semicolon": ";",
    "equals": "=",
    "multiply": "Multiply",
    "add": "Add",
    "separator": "Separator",
    "subtract": "Subtract",
    "decimal": "Decimal",
    "divide": "Divide",
    "capslock": "CapsLock",
    "f1": "F1",
    "f2": "F2",
    "f3": "F3",
    "f4": "F4",
    "f5": "F5",
    "f6": "F6",
    "f7": "F7",
    "f8": "F8",
    "f9": "F9",
    "f10": "F10",
    "f11": "F11",
    "f12": "F12",
    "command": "Meta",
    "cmd": "Meta",
    "meta": "Meta",
    "super": "Meta",
    "win": "Meta",
    "windows": "Meta",
}

DEFAULT_LAUNCH_ARGS = (
    "--disable-extensions",
    "--disable-file-system",
    "--disable-plugins",
    "--disable-dev-shm-usage",
    "--disable-background-networking",
    "--disable-default-apps",
    "--disable-sync",
)

TabMode = Literal["single", "follow"]

SAFE_SCHEMES = ("about", "data", "blob", "chrome-error")

BLOCKED_PAGE = (
    "<html><body style='font-family:sans-serif;padding:40px'>"
    "<h1>Blocked</h1><p>Navigation to <b>{host}</b> is not allowed by this "
    "agent's domain policy.</p></body></html>"
)


def _env_bool(name: str) -> Optional[bool]:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return None
    return value.strip().lower() in ("1", "true", "yes", "on")


def _domain_matches(host: str, pattern: str) -> bool:
    pattern = pattern.strip().lower().lstrip("*").lstrip(".")
    host = host.lower()
    return bool(pattern) and (host == pattern or host.endswith("." + pattern))


class DomainPolicy:
    """Allowlist / blocklist for top-level navigations."""

    def __init__(self, allowed: Sequence[str] = (), blocked: Sequence[str] = ()):
        self.allowed = [d for d in allowed if d.strip()]
        self.blocked = [d for d in blocked if d.strip()]

    @property
    def active(self) -> bool:
        return bool(self.allowed or self.blocked)

    def is_allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            # about:blank, data:, blob: and error pages are harmless; anything
            # else (file:, chrome:, view-source:, ...) is blocked by an active policy.
            return parsed.scheme in SAFE_SCHEMES or not self.active
        host = parsed.hostname or ""
        if any(_domain_matches(host, d) for d in self.blocked):
            return False
        if self.allowed:
            return any(_domain_matches(host, d) for d in self.allowed)
        return True


class PlaywrightComputer(Computer):
    """Launches and controls a local Chromium (or Chrome / Edge) browser."""

    environment = "browser"

    def __init__(
        self,
        screen_size: tuple[int, int] = (1440, 900),
        initial_url: str = "https://www.google.com",
        search_engine_url: str = "https://www.google.com",
        highlight_mouse: bool = False,
        *,
        headless: Optional[bool] = None,
        browser_channel: Optional[str] = None,
        executable_path: Optional[str] = None,
        user_data_dir: Optional[str] = None,
        proxy_server: Optional[str] = None,
        allowed_domains: Sequence[str] = (),
        blocked_domains: Sequence[str] = (),
        tab_mode: TabMode = "single",
        action_timeout_ms: int = 30_000,
        settle_time_s: float = 0.5,
    ):
        self._initial_url = initial_url
        self._screen_size = screen_size
        self._search_engine_url = search_engine_url
        self._highlight_mouse = highlight_mouse
        self._headless = headless
        self._browser_channel = browser_channel or os.environ.get("PLAYWRIGHT_CHANNEL")
        self._executable_path = executable_path
        self._user_data_dir = user_data_dir
        self._proxy_server = proxy_server
        self._domain_policy = DomainPolicy(allowed_domains, blocked_domains)
        self._tab_mode = tab_mode
        self._action_timeout_ms = action_timeout_ms
        self._settle_time_s = settle_time_s
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._page_stack: list = []
        self._pending_pages: list = []
        self._last_screenshot_size: Optional[tuple[int, int]] = None
        # Playwright resolves "ControlOrMeta" from the *client* OS. That is right
        # for a local browser; remote browsers set this from their own platform.
        self._control_key = "ControlOrMeta"

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def _resolve_headless(self) -> bool:
        headless = self._headless
        if headless is None:
            headless = _env_bool("PLAYWRIGHT_HEADLESS")
        no_display = (
            sys.platform.startswith("linux")
            and not os.environ.get("DISPLAY")
            and not os.environ.get("WAYLAND_DISPLAY")
        )
        if not headless and no_display:
            # A headed browser cannot start without a display (SSH, WSL, CI ...).
            termcolor.cprint(
                "No DISPLAY found; running the browser headless. Provide a display "
                "(or use the Docker image) to watch it.",
                color="yellow",
            )
            return True
        return bool(headless)

    def _connect(self) -> None:
        """Launches a local browser and sets _browser/_context/_page."""
        headless = self._resolve_headless()
        launch_kwargs = dict(
            args=list(DEFAULT_LAUNCH_ARGS),
            headless=headless,
        )
        if self._browser_channel and self._browser_channel != "chromium":
            launch_kwargs["channel"] = self._browser_channel
        if self._executable_path:
            launch_kwargs["executable_path"] = self._executable_path
        if self._proxy_server:
            launch_kwargs["proxy"] = {"server": self._proxy_server}
        viewport = {"width": self._screen_size[0], "height": self._screen_size[1]}

        if self._user_data_dir:
            # Persistent profile: cookies / logins survive between runs.
            self._context = self._playwright.chromium.launch_persistent_context(
                self._user_data_dir, viewport=viewport, **launch_kwargs
            )
            self._browser = None
            self._page = (
                self._context.pages[0] if self._context.pages else self._context.new_page()
            )
        else:
            self._browser = self._playwright.chromium.launch(**launch_kwargs)
            self._context = self._browser.new_context(viewport=viewport)
            self._page = self._context.new_page()
        termcolor.cprint(
            f"Started local {'headless ' if headless else ''}browser"
            f"{' (' + self._browser_channel + ')' if self._browser_channel else ''}.",
            color="green",
            attrs=["bold"],
        )

    def _disconnect(self) -> None:
        """Closes what `_connect` opened."""
        try:
            if self._context:
                self._context.close()
        except Exception:  # noqa: BLE001 - already gone
            pass
        try:
            if self._browser:
                self._browser.close()
        except Exception as e:  # noqa: BLE001
            # Browser was already shut down because of SIGINT or such.
            if "closed" not in str(e).lower():
                raise

    def __enter__(self):
        print("Creating session...")
        self._playwright = sync_playwright().start()
        try:
            self._connect()
            self._configure()
            if self._initial_url:
                self._goto(self._initial_url)
        except BaseException:
            self.__exit__(*sys.exc_info())
            raise
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            self._disconnect()
        finally:
            if self._playwright:
                self._playwright.stop()
                self._playwright = None

    def _configure(self) -> None:
        """Common setup once a context/page exists."""
        self._context.set_default_timeout(self._action_timeout_ms)
        self._context.on("page", self._handle_new_page)
        if self._domain_policy.active:
            self._context.route("**/*", self._route_request)

    # ------------------------------------------------------------------
    # tabs & navigation policy
    # ------------------------------------------------------------------

    def _opened_by_agent(self, new_page: playwright.sync_api.Page) -> bool:
        """Whether a new tab belongs to the agent. Overridden for shared browsers."""
        return True

    def _handle_new_page(self, new_page: playwright.sync_api.Page):
        # Event handlers must not block; the page is processed after the action.
        self._pending_pages.append(new_page)

    def _process_new_pages(self) -> None:
        """Computer Use works with a single visible tab.

        `single` (default): links that open a new tab are loaded in the current
        tab instead. `follow`: the agent switches to the new tab (keeps popups
        such as OAuth logins working) and returns to the previous tab when the
        new one closes.
        """
        while self._pending_pages:
            new_page = self._pending_pages.pop(0)
            if new_page.is_closed() or not self._opened_by_agent(new_page):
                continue
            if self._tab_mode == "follow":
                self._page_stack.append(self._page)
                self._page = new_page
                continue
            new_url = new_page.url
            if not new_url or new_url == "about:blank":
                try:
                    new_page.wait_for_url(lambda u: u != "about:blank", timeout=5_000)
                    new_url = new_page.url
                except Exception:  # noqa: BLE001 - the popup never navigated
                    pass
            new_page.close()
            if new_url and new_url != "about:blank":
                self._goto(new_url)
        if self._tab_mode == "follow" and self._page.is_closed():
            while self._page_stack:
                previous = self._page_stack.pop()
                if not previous.is_closed():
                    self._page = previous
                    break
        try:
            # Background tabs produce no frames, so screenshots would hang.
            self._page.bring_to_front()
        except Exception:  # noqa: BLE001
            pass

    def _route_request(self, route, request) -> None:
        # Never let an exception escape: an unanswered route hangs the page.
        try:
            is_main_frame = True
            try:
                is_main_frame = request.frame.parent_frame is None
            except Exception:  # noqa: BLE001 - e.g. a popup's first navigation has no frame yet
                pass
            if (
                request.is_navigation_request()
                and is_main_frame
                and not self._domain_policy.is_allowed(request.url)
            ):
                route.fulfill(
                    status=403,
                    content_type="text/html",
                    body=BLOCKED_PAGE.format(host=urlparse(request.url).hostname or request.url),
                )
                return
        except Exception:  # noqa: BLE001
            pass
        try:
            route.fallback()
        except Exception:  # noqa: BLE001 - page already closed
            pass

    def _enforce_domain_policy(self) -> None:
        """Re-checks the current URL: routes do not see redirect targets."""
        if not self._domain_policy.active or self._page.is_closed():
            return
        url = self._page.url
        if self._domain_policy.is_allowed(url):
            return
        termcolor.cprint(f"Blocked by domain policy: {url}", color="yellow")
        try:
            self._page.goto("about:blank")
            self._page.set_content(BLOCKED_PAGE.format(host=urlparse(url).hostname or url))
        except Exception:  # noqa: BLE001
            pass

    def _goto(self, url: str) -> None:
        try:
            self._page.goto(url, wait_until="domcontentloaded")
        except playwright.sync_api.TimeoutError:
            termcolor.cprint(f"Timed out loading {url}; continuing.", color="yellow")

    def _wait_for_load(self) -> None:
        if self._pending_pages or (self._page_stack and self._page.is_closed()):
            self._process_new_pages()
        try:
            self._page.wait_for_load_state(timeout=self._action_timeout_ms)
        except playwright.sync_api.TimeoutError:
            pass  # Long-polling pages never reach 'load'; use what we have.
        self._enforce_domain_policy()

    # ------------------------------------------------------------------
    # actions
    # ------------------------------------------------------------------

    def open_web_browser(self) -> EnvState:
        return self.current_state()

    def click_at(self, x: int, y: int) -> EnvState:
        self.highlight_mouse(x, y)
        self._page.mouse.click(x, y)
        self._wait_for_load()
        return self.current_state()

    def double_click_at(self, x: int, y: int) -> EnvState:
        self.highlight_mouse(x, y)
        self._page.mouse.dblclick(x, y)
        self._wait_for_load()
        return self.current_state()

    def triple_click_at(self, x: int, y: int) -> EnvState:
        self.highlight_mouse(x, y)
        self._page.mouse.click(x, y, click_count=3)
        self._wait_for_load()
        return self.current_state()

    def middle_click_at(self, x: int, y: int) -> EnvState:
        self.highlight_mouse(x, y)
        self._page.mouse.click(x, y, button="middle")
        self._wait_for_load()
        return self.current_state()

    def right_click_at(self, x: int, y: int) -> EnvState:
        self.highlight_mouse(x, y)
        self._page.mouse.click(x, y, button="right")
        self._wait_for_load()
        return self.current_state()

    def mouse_down(self, x: int, y: int) -> EnvState:
        self.highlight_mouse(x, y)
        self._page.mouse.move(x, y)
        self._page.mouse.down()
        return self.current_state()

    def mouse_up(self, x: int, y: int) -> EnvState:
        self.highlight_mouse(x, y)
        self._page.mouse.move(x, y)
        self._page.mouse.up()
        self._wait_for_load()
        return self.current_state()

    def hover_at(self, x: int, y: int) -> EnvState:
        self.highlight_mouse(x, y)
        self._page.mouse.move(x, y)
        return self.current_state()

    def type_text(
        self,
        text: str,
        press_enter: bool = False,
        x: Optional[int] = None,
        y: Optional[int] = None,
        clear_before_typing: bool = False,
    ) -> EnvState:
        if x is not None and y is not None:
            self.highlight_mouse(x, y)
            self._page.mouse.click(x, y)
        if clear_before_typing:
            self._page.keyboard.press(f"{self._control_key}+A")
            self._page.keyboard.press("Delete")
        self._page.keyboard.type(text)
        if press_enter:
            self._page.keyboard.press("Enter")
        self._wait_for_load()
        return self.current_state()

    def wait(self, seconds: float = 1) -> EnvState:
        self._page.wait_for_timeout(float(seconds) * 1000)
        return self.current_state()

    def _horizontal_document_scroll(self, direction: Literal["left", "right"]) -> EnvState:
        # Scroll by 50% of the viewport size.
        amount = self.screen_size()[0] // 2
        sign = "-" if direction == "left" else ""
        self._page.evaluate(f"window.scrollBy({sign}{amount}, 0);")
        self._wait_for_load()
        return self.current_state()

    def scroll_document(self, direction: Literal["up", "down", "left", "right"]) -> EnvState:
        if direction == "down":
            return self.key_combination(["PageDown"])
        if direction == "up":
            return self.key_combination(["PageUp"])
        if direction in ("left", "right"):
            return self._horizontal_document_scroll(direction)
        raise ValueError("Unsupported direction: ", direction)

    def scroll_at(
        self,
        x: int,
        y: int,
        direction: Literal["up", "down", "left", "right"],
        magnitude: int = 800,
    ) -> EnvState:
        self.highlight_mouse(x, y)
        self._page.mouse.move(x, y)
        dx, dy = {
            "up": (0, -magnitude),
            "down": (0, magnitude),
            "left": (-magnitude, 0),
            "right": (magnitude, 0),
        }.get(direction, (None, None))
        if dx is None:
            raise ValueError("Unsupported direction: ", direction)
        self._page.mouse.wheel(dx, dy)
        self._wait_for_load()
        return self.current_state()

    def go_back(self) -> EnvState:
        self._page.go_back()
        self._wait_for_load()
        return self.current_state()

    def go_forward(self) -> EnvState:
        self._page.go_forward()
        self._wait_for_load()
        return self.current_state()

    def search(self) -> EnvState:
        return self.navigate(self._search_engine_url)

    def navigate(self, url: str) -> EnvState:
        normalized_url = url.strip()
        if not normalized_url.startswith(("http://", "https://", "about:", "data:", "file:")):
            normalized_url = "https://" + normalized_url
        if not self._domain_policy.is_allowed(normalized_url):
            target = urlparse(normalized_url).hostname or normalized_url
            raise PermissionError(
                f"Navigation to {target} is blocked by the domain policy "
                "(--allowed_domains / --blocked_domains)."
            )
        self._goto(normalized_url)
        self._wait_for_load()
        return self.current_state()

    def _normalize_key(self, key: str) -> str:
        mapped = PLAYWRIGHT_KEY_MAP.get(key.lower(), key)
        return self._control_key if mapped == "ControlOrMeta" else mapped

    def key_combination(self, keys: list[str]) -> EnvState:
        keys = [self._normalize_key(k) for k in keys]
        if not keys:
            raise ValueError("key_combination requires at least one key")
        for key in keys[:-1]:
            self._page.keyboard.down(key)
        self._page.keyboard.press(keys[-1])
        for key in reversed(keys[:-1]):
            self._page.keyboard.up(key)
        self._wait_for_load()
        return self.current_state()

    def press_key(self, key: str) -> EnvState:
        return self.key_combination([key])

    def key_down(self, key: str) -> EnvState:
        self._page.keyboard.down(self._normalize_key(key))
        return self.current_state()

    def key_up(self, key: str) -> EnvState:
        self._page.keyboard.up(self._normalize_key(key))
        self._wait_for_load()
        return self.current_state()

    def take_screenshot(self) -> EnvState:
        return self.current_state()

    def drag_and_drop(
        self, x: int, y: int, destination_x: int, destination_y: int
    ) -> EnvState:
        self.highlight_mouse(x, y)
        self._page.mouse.move(x, y)
        self._page.mouse.down()
        self.highlight_mouse(destination_x, destination_y)
        self._page.mouse.move(destination_x, destination_y, steps=10)
        self._page.mouse.up()
        self._wait_for_load()
        return self.current_state()

    def current_state(self) -> EnvState:
        self._wait_for_load()
        # Even if Playwright reports the page as loaded, it may not be so.
        # Wait a moment for rendering. wait_for_timeout (unlike time.sleep) also
        # dispatches Playwright events, e.g. a new tab opened by the last click.
        if self._page.is_closed():
            self._process_new_pages()
        self._page.wait_for_timeout(self._settle_time_s * 1000)
        if self._pending_pages or self._page.is_closed():
            self._process_new_pages()
            self._wait_for_load()
        # scale="css" keeps the screenshot in CSS pixels, i.e. the same
        # coordinate space as mouse events, also on HiDPI / remote browsers.
        screenshot_bytes = self._page.screenshot(type="png", full_page=False, scale="css")
        # The PNG header holds the size; with scale="css" it equals the viewport.
        if screenshot_bytes[:8] == b"\x89PNG\r\n\x1a\n":
            self._last_screenshot_size = struct.unpack(">II", screenshot_bytes[16:24])
        return EnvState(screenshot=screenshot_bytes, url=self._page.url)

    def screen_size(self) -> tuple[int, int]:
        viewport_size = self._page.viewport_size if self._page else None
        if viewport_size:
            return viewport_size["width"], viewport_size["height"]
        if self._last_screenshot_size:
            # Remote browsers without viewport emulation: the size of the
            # screenshot the model just saw (no extra round trip per action).
            return self._last_screenshot_size
        if self._page:
            # Remote browsers without viewport emulation: ask the page.
            try:
                width, height = self._page.evaluate("() => [innerWidth, innerHeight]")
                if width and height:
                    return int(width), int(height)
            except Exception:  # noqa: BLE001
                pass
        return self._screen_size

    def live_view_url(self) -> Optional[str]:
        # Set by the Docker image (noVNC) when the browser runs on a virtual display.
        return os.environ.get("LIVE_VIEW_URL") or None

    def highlight_mouse(self, x: int, y: int):
        if not self._highlight_mouse:
            return
        self._page.evaluate(
            f"""
        () => {{
            const element_id = "playwright-feedback-circle";
            const div = document.createElement('div');
            div.id = element_id;
            div.style.pointerEvents = 'none';
            div.style.border = '4px solid red';
            div.style.borderRadius = '50%';
            div.style.width = '20px';
            div.style.height = '20px';
            div.style.position = 'fixed';
            div.style.zIndex = '9999';
            document.body.appendChild(div);

            div.hidden = false;
            div.style.left = {x} - 10 + 'px';
            div.style.top = {y} - 10 + 'px';

            setTimeout(() => {{
                div.hidden = true;
            }}, 2000);
        }}
    """
        )
        # Wait a bit for the user to see the cursor.
        time.sleep(1)

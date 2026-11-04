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
"""A Linux desktop environment (Computer Use `environment="desktop"`).

Controls an X11 display with `xdotool` and captures it with `mss`. The
intended setup is the Docker image in this repository, which runs a virtual
X display (Xvfb) with a window manager and a noVNC live view. It also works on
any Linux machine with an X11 session (set DISPLAY).

WARNING: the agent gets full mouse/keyboard control of the display. Only use
it inside a sandbox (container / VM), never on your personal desktop session.
"""
import os
import shutil
import subprocess
import time
from typing import Literal, Optional

import termcolor

from ..computer import Computer, EnvState

# Model key names -> X keysyms understood by xdotool.
XDOTOOL_KEY_MAP = {
    "enter": "Return",
    "return": "Return",
    "backspace": "BackSpace",
    "tab": "Tab",
    "escape": "Escape",
    "esc": "Escape",
    "space": "space",
    "spacebar": "space",
    "delete": "Delete",
    "del": "Delete",
    "insert": "Insert",
    "home": "Home",
    "end": "End",
    "pageup": "Page_Up",
    "page_up": "Page_Up",
    "pagedown": "Page_Down",
    "page_down": "Page_Down",
    "left": "Left",
    "arrowleft": "Left",
    "right": "Right",
    "arrowright": "Right",
    "up": "Up",
    "arrowup": "Up",
    "down": "Down",
    "arrowdown": "Down",
    "shift": "shift",
    "control": "ctrl",
    "ctrl": "ctrl",
    "controlormeta": "ctrl",
    "alt": "alt",
    "option": "alt",
    "meta": "super",
    "command": "super",
    "cmd": "super",
    "super": "super",
    "win": "super",
    "windows": "super",
    "capslock": "Caps_Lock",
    "printscreen": "Print",
    "semicolon": "semicolon",
    "equals": "equal",
    "minus": "minus",
    "plus": "plus",
    "comma": "comma",
    "period": "period",
    "slash": "slash",
    "backslash": "backslash",
    **{f"f{i}": f"F{i}" for i in range(1, 13)},
}

MODIFIERS = {"shift", "ctrl", "alt", "super"}


class DesktopComputer(Computer):
    """Controls an X11 desktop via xdotool."""

    environment = "desktop"

    def __init__(
        self,
        screen_size: Optional[tuple[int, int]] = None,
        display: Optional[str] = None,
        type_delay_ms: int = 12,
        settle_time_s: float = 0.6,
        scroll_pixels_per_click: int = 100,
    ):
        self._requested_screen_size = screen_size
        self._display = display or os.environ.get("DISPLAY")
        self._type_delay_ms = type_delay_ms
        self._settle_time_s = settle_time_s
        self._scroll_pixels_per_click = max(1, scroll_pixels_per_click)
        self._screen_size: Optional[tuple[int, int]] = None
        self._env: dict[str, str] = {}

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def __enter__(self):
        if not self._display:
            raise RuntimeError(
                "The desktop environment needs an X11 display. Run it in the Docker "
                "image (docker compose run --rm desktop ...) or set DISPLAY."
            )
        if not shutil.which("xdotool"):
            raise RuntimeError("xdotool is not installed (apt-get install xdotool).")
        self._env = dict(os.environ, DISPLAY=self._display)
        self._screen_size = self._detect_screen_size()
        if self._requested_screen_size and tuple(self._requested_screen_size) != self._screen_size:
            termcolor.cprint(
                f"Note: the display is {self._screen_size[0]}x{self._screen_size[1]}; "
                f"requested {self._requested_screen_size[0]}x{self._requested_screen_size[1]} "
                "is ignored (set SCREEN_WIDTH/SCREEN_HEIGHT for the Docker display).",
                color="yellow",
            )
        termcolor.cprint(
            f"Connected to desktop {self._display} ({self._screen_size[0]}x{self._screen_size[1]}).",
            color="green",
            attrs=["bold"],
        )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # Release anything the model may have left pressed.
        for key in MODIFIERS:
            self._xdotool("keyup", key, check=False)
        self._xdotool("mouseup", "1", check=False)

    def _detect_screen_size(self) -> tuple[int, int]:
        out = self._xdotool("getdisplaygeometry", capture=True)
        width, height = (int(v) for v in out.split())
        return width, height

    def live_view_url(self) -> Optional[str]:
        return os.environ.get("LIVE_VIEW_URL") or None

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _xdotool(self, *args: str, capture: bool = False, check: bool = True) -> str:
        result = subprocess.run(
            ["xdotool", *args],
            env=self._env or dict(os.environ),
            capture_output=True,
            text=True,
            timeout=60,
        )
        if check and result.returncode != 0:
            raise RuntimeError(f"xdotool {' '.join(args)} failed: {result.stderr.strip()}")
        return result.stdout.strip() if capture else ""

    @staticmethod
    def _keysym(key: str, in_combo: bool = False) -> str:
        mapped = XDOTOOL_KEY_MAP.get(key.lower())
        if mapped:
            return mapped
        if in_combo and len(key) == 1:
            # 'ctrl+C' would send ctrl+shift+c; use the plain key.
            return key.lower()
        return key

    def _move(self, x: int, y: int) -> None:
        self._xdotool("mousemove", "--sync", str(x), str(y))

    # ------------------------------------------------------------------
    # Computer interface
    # ------------------------------------------------------------------

    def screen_size(self) -> tuple[int, int]:
        if self._screen_size is None:
            self._screen_size = self._detect_screen_size()
        return self._screen_size

    def current_state(self) -> EnvState:
        time.sleep(self._settle_time_s)
        import mss
        import mss.tools

        factory = getattr(mss, "MSS", None) or mss.mss
        with factory(display=self._display) as sct:
            monitor = sct.monitors[0]  # the whole X screen
            image = sct.grab(monitor)
            png = mss.tools.to_png(image.rgb, image.size)
        metadata = {}
        title = self._xdotool("getactivewindow", "getwindowname", capture=True, check=False)
        if title:
            metadata["active_window"] = title
        return EnvState(screenshot=png, url=None, metadata=metadata)

    def _click(self, x: int, y: int, button: str = "1", repeat: int = 1) -> EnvState:
        self._move(x, y)
        args = ["click"]
        if repeat > 1:
            args += ["--repeat", str(repeat), "--delay", "80"]
        self._xdotool(*args, button)
        return self.current_state()

    def click_at(self, x: int, y: int) -> EnvState:
        return self._click(x, y)

    def double_click_at(self, x: int, y: int) -> EnvState:
        return self._click(x, y, repeat=2)

    def triple_click_at(self, x: int, y: int) -> EnvState:
        return self._click(x, y, repeat=3)

    def middle_click_at(self, x: int, y: int) -> EnvState:
        return self._click(x, y, button="2")

    def right_click_at(self, x: int, y: int) -> EnvState:
        return self._click(x, y, button="3")

    def mouse_down(self, x: int, y: int) -> EnvState:
        self._move(x, y)
        self._xdotool("mousedown", "1")
        return self.current_state()

    def mouse_up(self, x: int, y: int) -> EnvState:
        self._move(x, y)
        self._xdotool("mouseup", "1")
        return self.current_state()

    def hover_at(self, x: int, y: int) -> EnvState:
        self._move(x, y)
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
            self._move(x, y)
            self._xdotool("click", "1")
        if clear_before_typing:
            self._xdotool("key", "--clearmodifiers", "ctrl+a")
            self._xdotool("key", "--clearmodifiers", "BackSpace")
        if text:
            self._xdotool("type", "--clearmodifiers", "--delay", str(self._type_delay_ms), "--", text)
        if press_enter:
            self._xdotool("key", "--clearmodifiers", "Return")
        return self.current_state()

    def scroll_at(
        self,
        x: int,
        y: int,
        direction: Literal["up", "down", "left", "right"],
        magnitude: int,
    ) -> EnvState:
        button = {"up": "4", "down": "5", "left": "6", "right": "7"}.get(direction)
        if button is None:
            raise ValueError(f"Unsupported direction: {direction}")
        clicks = max(1, round(magnitude / self._scroll_pixels_per_click))
        self._move(x, y)
        self._xdotool("click", "--repeat", str(clicks), "--delay", "30", button)
        return self.current_state()

    def key_combination(self, keys: list[str]) -> EnvState:
        if not keys:
            raise ValueError("key_combination requires at least one key")
        combo = "+".join(self._keysym(k, in_combo=len(keys) > 1) for k in keys)
        self._xdotool("key", "--clearmodifiers", combo)
        return self.current_state()

    def key_down(self, key: str) -> EnvState:
        self._xdotool("keydown", self._keysym(key))
        return self.current_state()

    def key_up(self, key: str) -> EnvState:
        self._xdotool("keyup", self._keysym(key))
        return self.current_state()

    def drag_and_drop(
        self, x: int, y: int, destination_x: int, destination_y: int
    ) -> EnvState:
        self._move(x, y)
        self._xdotool("mousedown", "1")
        # Move in a few steps so drag handlers see intermediate motion events.
        steps = 8
        for i in range(1, steps + 1):
            ix = x + (destination_x - x) * i // steps
            iy = y + (destination_y - y) * i // steps
            self._move(ix, iy)
            time.sleep(0.02)
        self._xdotool("mouseup", "1")
        return self.current_state()

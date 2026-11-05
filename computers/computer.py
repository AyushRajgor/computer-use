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
"""The `Computer` interface that every execution environment implements.

The agent translates the model's (normalized) actions into calls on this
interface. Each call returns an `EnvState` -- a screenshot plus some metadata --
which is sent back to the model as the function result.

Only a handful of primitives are abstract. Higher level actions (legacy
`type_text_at`, `scroll_document`, `search`, ...) have default implementations
composed from those primitives, so new environments stay small.
"""
import abc
import time
from typing import Any, ClassVar, Literal, Optional

import pydantic

Direction = Literal["up", "down", "left", "right"]
EnvironmentName = Literal["browser", "desktop"]


class EnvState(pydantic.BaseModel):
    """The state of the environment after an action."""

    # The screenshot in PNG format.
    screenshot: bytes
    # The current URL (browser environments only).
    url: Optional[str] = None
    # Extra, environment specific information that is sent to the model
    # together with the screenshot (e.g. the active window title on desktop).
    metadata: dict[str, Any] = pydantic.Field(default_factory=dict)


class UnsupportedActionError(NotImplementedError):
    """Raised when an environment does not support the requested action."""


class Computer(abc.ABC):
    """Defines an interface for environments.

    All coordinates passed to these methods are absolute pixel values, already
    scaled from the model's normalized 0-999 grid to `screen_size()`.
    """

    # Which Computer Use environment this computer implements. This is sent to
    # the model in the `computer_use` tool declaration.
    environment: ClassVar[EnvironmentName] = "browser"

    # ---- lifecycle -------------------------------------------------------

    def __enter__(self) -> "Computer":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        return None

    def live_view_url(self) -> Optional[str]:
        """A URL where a human can watch (or take over) the session, if any."""
        return None

    # ---- primitives ------------------------------------------------------

    @abc.abstractmethod
    def screen_size(self) -> tuple[int, int]:
        """Returns the (width, height) of the screen in pixels."""

    @abc.abstractmethod
    def current_state(self) -> EnvState:
        """Returns the current state (screenshot, url, ...) of the environment."""

    @abc.abstractmethod
    def click_at(self, x: int, y: int) -> EnvState:
        """Left clicks at a specific x, y coordinate."""

    @abc.abstractmethod
    def double_click_at(self, x: int, y: int) -> EnvState:
        """Double clicks at a specific x, y coordinate."""

    @abc.abstractmethod
    def triple_click_at(self, x: int, y: int) -> EnvState:
        """Triple clicks at a specific x, y coordinate."""

    @abc.abstractmethod
    def middle_click_at(self, x: int, y: int) -> EnvState:
        """Middle clicks at a specific x, y coordinate."""

    @abc.abstractmethod
    def right_click_at(self, x: int, y: int) -> EnvState:
        """Right clicks at a specific x, y coordinate."""

    @abc.abstractmethod
    def mouse_down(self, x: int, y: int) -> EnvState:
        """Presses and holds the left mouse button at a specific x, y coordinate."""

    @abc.abstractmethod
    def mouse_up(self, x: int, y: int) -> EnvState:
        """Releases the left mouse button at a specific x, y coordinate."""

    @abc.abstractmethod
    def hover_at(self, x: int, y: int) -> EnvState:
        """Moves the mouse to a specific x, y coordinate."""

    @abc.abstractmethod
    def type_text(
        self,
        text: str,
        press_enter: bool = False,
        x: Optional[int] = None,
        y: Optional[int] = None,
        clear_before_typing: bool = False,
    ) -> EnvState:
        """Types text into the focused element.

        If `x` and `y` are given, the element at that position is clicked first.
        If `clear_before_typing` is set, existing content is selected and deleted.
        If `press_enter` is set, ENTER is pressed after typing.
        """

    @abc.abstractmethod
    def scroll_at(
        self,
        x: int,
        y: int,
        direction: Direction,
        magnitude: int,
    ) -> EnvState:
        """Scrolls `magnitude` pixels in `direction` with the mouse at x, y."""

    @abc.abstractmethod
    def key_combination(self, keys: list[str]) -> EnvState:
        """Presses a key combination, e.g. ["Control", "C"] or ["Enter"]."""

    @abc.abstractmethod
    def key_down(self, key: str) -> EnvState:
        """Presses and holds a keyboard key."""

    @abc.abstractmethod
    def key_up(self, key: str) -> EnvState:
        """Releases a keyboard key."""

    @abc.abstractmethod
    def drag_and_drop(
        self, x: int, y: int, destination_x: int, destination_y: int
    ) -> EnvState:
        """Drags from (x, y) and drops at (destination_x, destination_y)."""

    # ---- browser-only primitives (desktop environments raise) ------------

    def navigate(self, url: str) -> EnvState:
        """Navigates directly to a specified URL."""
        raise UnsupportedActionError(
            f"'navigate' is not supported in the {self.environment} environment."
        )

    def go_back(self) -> EnvState:
        """Navigates back in the browser history."""
        raise UnsupportedActionError(
            f"'go_back' is not supported in the {self.environment} environment."
        )

    def go_forward(self) -> EnvState:
        """Navigates forward in the browser history."""
        raise UnsupportedActionError(
            f"'go_forward' is not supported in the {self.environment} environment."
        )

    # ---- composed actions -------------------------------------------------

    def open_web_browser(self) -> EnvState:
        """Opens the web browser (it is always open; returns the current state)."""
        return self.current_state()

    def take_screenshot(self) -> EnvState:
        """Takes a screenshot of the current screen."""
        return self.current_state()

    def wait(self, seconds: float = 1) -> EnvState:
        """Waits for a duration to let unfinished processes complete."""
        time.sleep(max(0.0, float(seconds)))
        return self.current_state()

    def wait_5_seconds(self) -> EnvState:
        """Waits for 5 seconds (legacy action)."""
        return self.wait(5)

    def press_key(self, key: str) -> EnvState:
        """Presses and releases a single key."""
        return self.key_combination([key])

    def type_text_at(
        self,
        x: int,
        y: int,
        text: str,
        press_enter: bool = False,
        clear_before_typing: bool = True,
    ) -> EnvState:
        """Clicks at x, y and types text (legacy action)."""
        return self.type_text(
            text=text,
            press_enter=press_enter,
            x=x,
            y=y,
            clear_before_typing=clear_before_typing,
        )

    def scroll_document(self, direction: Direction) -> EnvState:
        """Scrolls the whole page/screen by roughly half a screen (legacy action)."""
        width, height = self.screen_size()
        magnitude = (height if direction in ("up", "down") else width) // 2
        return self.scroll_at(width // 2, height // 2, direction, magnitude)

    def search(self) -> EnvState:
        """Jumps to a search engine home page (legacy action)."""
        return self.navigate("https://www.google.com")

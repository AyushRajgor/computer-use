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
"""Attach to any browser that exposes the Chrome DevTools Protocol (CDP).

Use this for a browser you run yourself, for example:

* the Chrome you are using right now, with your normal profile and logins:
  enable chrome://inspect/#remote-debugging, then  --env cdp  (the default
  target is "chrome"; also chrome-beta, msedge, ...). Chrome asks you to allow
  each connection. The agent works in a new tab and leaves your tabs alone.
* a separately started Chrome:  chrome --remote-debugging-port=9222 --user-data-dir=/tmp/cu
  then  --env cdp --cdp_url http://localhost:9222
* a self-hosted browser container (browserless, steel-browser, ...), or
* any cloud provider that hands out a CDP websocket URL.

The browser is *not* closed on exit -- we only disconnect from it.
"""
from typing import Optional

import termcolor

from ..playwright.playwright import PlaywrightComputer

# Browser names Playwright resolves to the *running* browser of the user's
# default profile, via the DevToolsActivePort file that Chrome writes once
# chrome://inspect/#remote-debugging is enabled.
LOCAL_BROWSER_CHANNELS = (
    "chrome",
    "chrome-beta",
    "chrome-dev",
    "chrome-canary",
    "msedge",
    "msedge-beta",
    "msedge-dev",
    "msedge-canary",
)
DEFAULT_CDP_URL = "chrome"


class CDPComputer(PlaywrightComputer):
    """Drives an existing browser over CDP via Playwright."""

    def __init__(
        self,
        cdp_url: Optional[str] = None,
        *args,
        cdp_headers: Optional[dict[str, str]] = None,
        reuse_context: bool = True,
        set_viewport: bool = True,
        connect_timeout_ms: int = 60_000,
        own_tab: Optional[bool] = None,
        **kwargs,
    ):
        """
        Args:
            cdp_url: A CDP http(s)/ws(s) URL, or the name of a running local
                browser (see LOCAL_BROWSER_CHANNELS). Default: "chrome".
            own_tab: Work in a new tab, leave it open on exit and ignore tabs
                the agent did not open. Default: on when attaching to a
                local browser by name, i.e. a browser somebody is using.
        """
        super().__init__(*args, **kwargs)
        self._cdp_url = cdp_url
        self._cdp_headers = cdp_headers
        self._reuse_context = reuse_context
        self._set_viewport = set_viewport
        self._connect_timeout_ms = connect_timeout_ms
        self._own_tab_option = own_tab
        self._own_tab = False
        self._owns_context = False
        self._owns_page = False

    def _cdp_endpoint(self) -> str:
        """Returns the CDP URL to connect to. Cloud subclasses create a session here."""
        return self._cdp_url or DEFAULT_CDP_URL

    def _connect(self) -> None:
        endpoint = self._cdp_endpoint()
        local_browser = endpoint.strip().lower() in LOCAL_BROWSER_CHANNELS
        if local_browser:
            # Playwright matches browser names case-sensitively ("Chrome" fails).
            endpoint = endpoint.strip().lower()
        self._own_tab = local_browser if self._own_tab_option is None else self._own_tab_option
        if local_browser:
            termcolor.cprint(
                f"Attaching to your running {endpoint}. Click 'Allow' if it asks.",
                color="cyan",
            )
        self._browser = self._playwright.chromium.connect_over_cdp(
            endpoint, timeout=self._connect_timeout_ms, headers=self._cdp_headers
        )
        viewport = {"width": self._screen_size[0], "height": self._screen_size[1]}
        if self._reuse_context and self._browser.contexts:
            # The default context holds the profile (cookies, logins, ...).
            self._context = self._browser.contexts[0]
            self._owns_context = False
        else:
            self._context = self._browser.new_context(viewport=viewport)
            self._owns_context = True
        if self._own_tab:
            # Somebody's own browser: work in a tab of our own, never in theirs,
            # and leave it open on exit so the result stays visible.
            self._page = self._context.new_page()
            self._owns_page = False
        elif self._context.pages:
            self._page = self._context.pages[0]
            self._owns_page = False
        else:
            self._page = self._context.new_page()
            self._owns_page = True
        if self._set_viewport and not self._owns_context:
            try:
                self._page.set_viewport_size(viewport)
            except Exception as e:  # noqa: BLE001 - some providers lock the viewport
                termcolor.cprint(f"Could not set viewport ({e}); using the browser's.", color="yellow")
        self._control_key = self._detect_control_key()
        termcolor.cprint(
            f"Connected to browser over CDP ({self._describe_endpoint()}).",
            color="green",
            attrs=["bold"],
        )

    def _opened_by_agent(self, new_page) -> bool:
        if not self._own_tab:
            return True
        # In somebody's own browser only popups of the agent's tabs are the
        # agent's business; tabs the user opens meanwhile are left alone.
        try:
            opener = new_page.opener()
        except Exception:  # noqa: BLE001
            return False
        return opener is not None and any(
            opener == page for page in (self._page, *self._page_stack)
        )

    def current_state(self):
        if self._own_tab:
            # The user may have switched tabs; background tabs produce no frames.
            try:
                self._page.bring_to_front()
            except Exception:  # noqa: BLE001
                pass
        return super().current_state()

    def _detect_control_key(self) -> str:
        """'Meta' if the remote browser runs on macOS, else 'Control'."""
        try:
            platform = self._page.evaluate(
                "() => (navigator.userAgentData && navigator.userAgentData.platform)"
                " || navigator.platform || ''"
            )
        except Exception:  # noqa: BLE001
            return "Control"
        return "Meta" if "mac" in str(platform).lower() else "Control"

    def _describe_endpoint(self) -> str:
        # Never print query strings: they often carry API keys.
        return (self._cdp_url or DEFAULT_CDP_URL).split("?", 1)[0]

    def _disconnect(self) -> None:
        try:
            if self._owns_context and self._context:
                self._context.close()
            elif self._owns_page and self._page and not self._page.is_closed():
                self._page.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._browser:
                # For connect_over_cdp this disconnects; it does not kill the browser.
                self._browser.close()
        except Exception:  # noqa: BLE001
            pass

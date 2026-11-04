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

* your own Chrome:  google-chrome --remote-debugging-port=9222 --user-data-dir=/tmp/cu
  then  --env cdp --cdp_url http://localhost:9222
* a self-hosted browser container (browserless, steel-browser, ...), or
* any cloud provider that hands out a CDP websocket URL.

The browser is *not* closed on exit -- we only disconnect from it.
"""
from typing import Optional

import termcolor

from ..playwright.playwright import PlaywrightComputer


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
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._cdp_url = cdp_url
        self._cdp_headers = cdp_headers
        self._reuse_context = reuse_context
        self._set_viewport = set_viewport
        self._connect_timeout_ms = connect_timeout_ms
        self._owns_context = False
        self._owns_page = False

    def _cdp_endpoint(self) -> str:
        """Returns the CDP URL to connect to. Cloud subclasses create a session here."""
        if not self._cdp_url:
            raise ValueError("A CDP URL is required (--cdp_url or CDP_URL).")
        return self._cdp_url

    def _connect(self) -> None:
        endpoint = self._cdp_endpoint()
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
        if self._context.pages:
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
        termcolor.cprint(
            f"Connected to browser over CDP ({self._describe_endpoint()}).",
            color="green",
            attrs=["bold"],
        )

    def _describe_endpoint(self) -> str:
        # Never print query strings: they often carry API keys.
        return (self._cdp_url or "").split("?", 1)[0]

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

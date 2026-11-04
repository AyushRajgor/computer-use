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
"""Browser Use Cloud browser (https://docs.browser-use.com/cloud/browser/quickstart).

Creates a standalone cloud browser with `browser-use-sdk` (v4 API), attaches
Playwright over CDP and stops the browser on exit (closing the CDP connection
alone does not stop it). The Gemini Computer Use agent stays in charge of all
decisions; Browser Use only provides the (stealth, proxied) browser.
"""
import os
import time
from typing import Any, Optional

import termcolor

from ..cdp.cdp import CDPComputer


def _env_bool(name: str) -> Optional[bool]:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return None
    return value.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str) -> Optional[int]:
    value = os.environ.get(name)
    return int(value) if value and value.strip() else None


def _env_str(name: str) -> Optional[str]:
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else None


_UNSET = object()


class BrowserUseComputer(CDPComputer):
    """A Browser Use Cloud browser controlled through Playwright."""

    def __init__(
        self,
        screen_size: tuple[int, int] = (1440, 900),
        initial_url: str = "https://www.google.com",
        *args,
        api_key: Optional[str] = None,
        profile_id: Optional[str] = None,
        proxy_country_code: Any = _UNSET,
        session_timeout_min: Optional[int] = None,
        solve_captchas: Optional[bool] = None,
        enable_recording: Optional[bool] = None,
        allow_resizing: Optional[bool] = None,
        extension_ids: Optional[list[str]] = None,
        custom_proxy: Optional[str] = None,
        metadata: Optional[dict[str, str]] = None,
        **kwargs,
    ):
        # The browser is created with the requested screen size; emulating a
        # viewport over CDP would weaken its stealth fingerprint.
        kwargs.setdefault("set_viewport", False)
        super().__init__(None, screen_size, initial_url, *args, **kwargs)
        self._api_key = api_key or os.environ.get("BROWSER_USE_API_KEY")
        self._profile_id = profile_id or _env_str("BROWSER_USE_PROFILE_ID")
        if proxy_country_code is _UNSET:
            env_proxy = os.environ.get("BROWSER_USE_PROXY_COUNTRY")
            if env_proxy is None or env_proxy.strip() == "":
                proxy_country_code = _UNSET  # server default ("us")
            elif env_proxy.strip().lower() in ("none", "off", "false", "0"):
                proxy_country_code = None  # disable the proxy
            else:
                proxy_country_code = env_proxy.strip().lower()
        self._proxy_country_code = proxy_country_code
        self._session_timeout_min = session_timeout_min or _env_int("BROWSER_USE_SESSION_TIMEOUT")
        self._solve_captchas = (
            solve_captchas if solve_captchas is not None else _env_bool("BROWSER_USE_SOLVE_CAPTCHAS")
        )
        self._enable_recording = (
            enable_recording
            if enable_recording is not None
            else _env_bool("BROWSER_USE_ENABLE_RECORDING")
        )
        self._allow_resizing = (
            allow_resizing if allow_resizing is not None else _env_bool("BROWSER_USE_ALLOW_RESIZING")
        )
        env_ext = _env_str("BROWSER_USE_EXTENSION_IDS")
        self._extension_ids = extension_ids or (
            [e.strip() for e in env_ext.split(",") if e.strip()] if env_ext else None
        )
        self._custom_proxy = custom_proxy or _env_str("BROWSER_USE_CUSTOM_PROXY")
        self._metadata = metadata
        self._client = None
        self._session = None

    def session_create_params(self) -> dict[str, Any]:
        """Keyword arguments for `client.browsers.create` (exposed for tests)."""
        params: dict[str, Any] = {
            "browser_screen_width": self._screen_size[0],
            "browser_screen_height": self._screen_size[1],
        }
        if self._proxy_country_code is not _UNSET:
            params["proxy_country_code"] = self._proxy_country_code
        for key, value in (
            ("profile_id", self._profile_id),
            ("timeout", self._session_timeout_min),
            ("solve_captchas", self._solve_captchas),
            ("enable_recording", self._enable_recording),
            ("allow_resizing", self._allow_resizing),
            ("extension_ids", self._extension_ids),
        ):
            if value is not None:
                params[key] = value
        metadata = {"source": "gemini-computer-use"}
        metadata.update(self._metadata or {})
        params["metadata"] = metadata
        if self._custom_proxy:
            params["custom_proxy"] = self._parse_custom_proxy(self._custom_proxy)
        return params

    @staticmethod
    def _parse_custom_proxy(value: str):
        """Parses `[user:pass@]host:port` into a `CustomProxy`."""
        from browser_use_sdk.v4 import CustomProxy

        value = value.split("://", 1)[-1]
        username = password = None
        if "@" in value:
            creds, value = value.rsplit("@", 1)
            username, _, password = creds.partition(":")
        host, _, port = value.rpartition(":")
        if not host or not port.isdigit():
            raise ValueError("BROWSER_USE_CUSTOM_PROXY must look like [user:pass@]host:port")
        return CustomProxy(host=host, port=int(port), username=username or None, password=password or None)

    def _cdp_endpoint(self) -> str:
        return self.start_session()

    def start_session(self) -> str:
        """Creates the cloud browser and returns its CDP URL."""
        from browser_use_sdk.v4 import BrowserUse  # optional dependency

        if not self._api_key:
            raise ValueError("BROWSER_USE_API_KEY is not set.")
        self._client = BrowserUse(api_key=self._api_key)
        self._session = self._client.browsers.create(**self.session_create_params())
        if not self._session.cdp_url:
            self.stop_session()
            raise RuntimeError("Browser Use did not return a CDP URL for the new browser.")
        self._cdp_url = self._session.cdp_url
        termcolor.cprint(f"Browser Use cloud browser started: {self._session.id}", color="green", attrs=["bold"])
        return self._cdp_url

    def _describe_endpoint(self) -> str:
        return f"Browser Use browser {self._session.id}" if self._session else "Browser Use"

    def stop_session(self) -> None:
        """Stops the cloud browser (closing CDP alone does not stop it)."""
        if not (self._client and self._session) or getattr(self, "_stopped", False):
            return
        self._stopped = True
        try:
            self._client.browsers.stop(self._session.id)
            termcolor.cprint("Browser Use cloud browser stopped.", color="green")
        except Exception as e:  # noqa: BLE001
            termcolor.cprint(f"Could not stop Browser Use browser {self._session.id}: {e}", color="yellow")
            return
        if self._enable_recording:
            self._print_recording_url()

    def _print_recording_url(self, attempts: int = 5, delay_s: float = 2.0) -> None:
        for _ in range(attempts):
            try:
                view = self._client.browsers.get(self._session.id)
            except Exception:  # noqa: BLE001
                return
            if view.recording_url:
                termcolor.cprint(f"Recording: {view.recording_url}", color="green")
                return
            if view.recording_available is False:
                return
            time.sleep(delay_s)
        termcolor.cprint(
            "The recording is still processing; fetch it later with "
            f"client.browsers.get('{self._session.id}').recording_url",
            color="yellow",
        )

    def _disconnect(self) -> None:
        try:
            super()._disconnect()
        finally:
            self.stop_session()

    def live_view_url(self) -> Optional[str]:
        return self._session.live_url if self._session else None

    @property
    def session_id(self) -> Optional[str]:
        return str(self._session.id) if self._session else None

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
"""Browserbase cloud browser (https://docs.browserbase.com).

Creates a Browserbase session with the Python SDK, attaches Playwright over
CDP, and releases the session on exit. Every option can be set via the
constructor or `BROWSERBASE_*` environment variables (see .env.example).
"""
import os
import pathlib
from typing import Any, Optional

import termcolor

from ..cdp.cdp import CDPComputer

REGIONS = ("us-west-2", "us-east-1", "eu-central-1", "ap-southeast-1")
OS_CHOICES = ("windows", "mac", "linux", "mobile", "tablet")


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


class BrowserbaseComputer(CDPComputer):
    """A Browserbase session controlled through Playwright."""

    def __init__(
        self,
        screen_size: tuple[int, int] = (1440, 900),
        initial_url: str = "https://www.google.com",
        *args,
        api_key: Optional[str] = None,
        project_id: Optional[str] = None,
        region: Optional[str] = None,
        proxies: Optional[bool] = None,
        proxy_country: Optional[str] = None,
        proxy_state: Optional[str] = None,
        proxy_city: Optional[str] = None,
        solve_captchas: Optional[bool] = None,
        block_ads: Optional[bool] = None,
        record_session: Optional[bool] = None,
        log_session: Optional[bool] = None,
        verified: Optional[bool] = None,
        browser_os: Optional[str] = None,
        context_id: Optional[str] = None,
        persist_context: Optional[bool] = None,
        extension_id: Optional[str] = None,
        keep_alive: Optional[bool] = None,
        session_timeout_s: Optional[int] = None,
        ignore_certificate_errors: Optional[bool] = None,
        user_metadata: Optional[dict[str, Any]] = None,
        downloads_dir: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(None, screen_size, initial_url, *args, **kwargs)
        e = os.environ
        self._api_key = api_key or e.get("BROWSERBASE_API_KEY")
        self._project_id = project_id or _env_str("BROWSERBASE_PROJECT_ID")
        self._region = region or _env_str("BROWSERBASE_REGION")
        self._proxies = proxies if proxies is not None else _env_bool("BROWSERBASE_PROXIES")
        self._proxy_country = proxy_country or _env_str("BROWSERBASE_PROXY_COUNTRY")
        self._proxy_state = proxy_state or _env_str("BROWSERBASE_PROXY_STATE")
        self._proxy_city = proxy_city or _env_str("BROWSERBASE_PROXY_CITY")
        self._solve_captchas = (
            solve_captchas if solve_captchas is not None else _env_bool("BROWSERBASE_SOLVE_CAPTCHAS")
        )
        self._block_ads = block_ads if block_ads is not None else _env_bool("BROWSERBASE_BLOCK_ADS")
        self._record_session = (
            record_session if record_session is not None else _env_bool("BROWSERBASE_RECORD_SESSION")
        )
        self._log_session = (
            log_session if log_session is not None else _env_bool("BROWSERBASE_LOG_SESSION")
        )
        self._verified = verified if verified is not None else _env_bool("BROWSERBASE_VERIFIED")
        self._browser_os = browser_os or _env_str("BROWSERBASE_OS")
        self._context_id = context_id or _env_str("BROWSERBASE_CONTEXT_ID")
        self._persist_context = (
            persist_context if persist_context is not None else _env_bool("BROWSERBASE_PERSIST_CONTEXT")
        )
        self._extension_id = extension_id or _env_str("BROWSERBASE_EXTENSION_ID")
        self._keep_alive = keep_alive if keep_alive is not None else _env_bool("BROWSERBASE_KEEP_ALIVE")
        self._session_timeout_s = session_timeout_s or _env_int("BROWSERBASE_SESSION_TIMEOUT")
        self._ignore_certificate_errors = (
            ignore_certificate_errors
            if ignore_certificate_errors is not None
            else _env_bool("BROWSERBASE_IGNORE_CERTIFICATE_ERRORS")
        )
        self._user_metadata = user_metadata
        self._downloads_dir = downloads_dir or _env_str("BROWSERBASE_DOWNLOADS_DIR")
        self._bb = None
        self._session = None
        self._live_url: Optional[str] = None

        if self._region and self._region not in REGIONS:
            raise ValueError(f"BROWSERBASE_REGION must be one of {REGIONS}")
        if self._browser_os and self._browser_os not in OS_CHOICES:
            raise ValueError(f"BROWSERBASE_OS must be one of {OS_CHOICES}")

    # ------------------------------------------------------------------

    def session_create_params(self) -> dict[str, Any]:
        """The keyword arguments passed to `sessions.create` (exposed for tests)."""
        browser_settings: dict[str, Any] = {}
        if not self._verified:
            # Verified sessions control their own fingerprint/viewport.
            browser_settings["viewport"] = {
                "width": self._screen_size[0],
                "height": self._screen_size[1],
            }
        for key, value in (
            ("solve_captchas", self._solve_captchas),
            ("block_ads", self._block_ads),
            ("record_session", self._record_session),
            ("log_session", self._log_session),
            ("verified", self._verified),
            ("os", self._browser_os),
            ("ignore_certificate_errors", self._ignore_certificate_errors),
        ):
            if value is not None:
                browser_settings[key] = value
        if self._context_id:
            browser_settings["context"] = {
                "id": self._context_id,
                "persist": bool(self._persist_context),
            }
        if self._domain_policy.allowed:
            # Enforce the allowlist in the browser as well.
            browser_settings["allowed_domains"] = list(self._domain_policy.allowed)

        params: dict[str, Any] = {"browser_settings": browser_settings}
        if self._project_id:
            params["project_id"] = self._project_id
        if self._region:
            params["region"] = self._region
        if self._proxy_country or self._proxy_state or self._proxy_city:
            geolocation: dict[str, str] = {"country": (self._proxy_country or "US").upper()}
            if self._proxy_state:
                geolocation["state"] = self._proxy_state.upper()
            if self._proxy_city:
                geolocation["city"] = self._proxy_city.upper()
            params["proxies"] = [{"type": "browserbase", "geolocation": geolocation}]
        elif self._proxies is not None:
            params["proxies"] = self._proxies
        if self._extension_id:
            params["extension_id"] = self._extension_id
        if self._keep_alive is not None:
            params["keep_alive"] = self._keep_alive
        if self._session_timeout_s:
            params["api_timeout"] = self._session_timeout_s
        metadata = {"source": "gemini-computer-use"}
        metadata.update(self._user_metadata or {})
        params["user_metadata"] = metadata
        return params

    def _cdp_endpoint(self) -> str:
        return self.start_session()

    def start_session(self) -> str:
        """Creates the Browserbase session and returns its CDP connect URL."""
        import browserbase  # imported lazily so the dependency stays optional

        if not self._api_key:
            raise ValueError("BROWSERBASE_API_KEY is not set.")
        self._bb = browserbase.Browserbase(api_key=self._api_key)
        self._session = self._bb.sessions.create(**self.session_create_params())
        self._cdp_url = self._session.connect_url
        termcolor.cprint(
            f"Browserbase session started: https://browserbase.com/sessions/{self._session.id}",
            color="green",
            attrs=["bold"],
        )
        try:
            live = self._bb.sessions.debug(self._session.id)
            self._live_url = live.debugger_fullscreen_url
        except Exception as e:  # noqa: BLE001 - live view is a nice-to-have
            termcolor.cprint(f"Could not get live view URL: {e}", color="yellow")
        return self._cdp_url

    def _describe_endpoint(self) -> str:
        return f"Browserbase session {self._session.id}" if self._session else "Browserbase"

    def _connect(self) -> None:
        super()._connect()
        # Downloads are only stored by Browserbase when this is set.
        try:
            cdp = self._browser.new_browser_cdp_session()
            cdp.send(
                "Browser.setDownloadBehavior",
                {"behavior": "allow", "downloadPath": "downloads", "eventsEnabled": True},
            )
        except Exception as e:  # noqa: BLE001
            termcolor.cprint(f"Could not enable downloads: {e}", color="yellow")

    def _disconnect(self) -> None:
        try:
            super()._disconnect()
        finally:
            self.stop_session()

    def stop_session(self) -> None:
        """Saves downloads (if configured) and releases the session."""
        if not (self._bb and self._session) or getattr(self, "_released", False):
            return
        self._released = True
        if self._downloads_dir:
            try:
                path = pathlib.Path(self._downloads_dir)
                path.mkdir(parents=True, exist_ok=True)
                target = path / f"browserbase-{self._session.id}-downloads.zip"
                self._bb.sessions.downloads.list(self._session.id).write_to_file(target)
                termcolor.cprint(f"Saved session downloads to {target}", color="green")
            except Exception as e:  # noqa: BLE001
                termcolor.cprint(f"Could not fetch downloads: {e}", color="yellow")
        try:
            self._bb.sessions.update(self._session.id, status="REQUEST_RELEASE")
            termcolor.cprint("Browserbase session released.", color="green")
        except Exception as e:  # noqa: BLE001
            termcolor.cprint(f"Could not release Browserbase session: {e}", color="yellow")

    def live_view_url(self) -> Optional[str]:
        return self._live_url

    @property
    def session_id(self) -> Optional[str]:
        return self._session.id if self._session else None


def create_context(api_key: Optional[str] = None, project_id: Optional[str] = None) -> str:
    """Creates a persistent Browserbase context and returns its id.

    Use the id with BROWSERBASE_CONTEXT_ID and BROWSERBASE_PERSIST_CONTEXT=true
    to keep cookies / logins between sessions.
    """
    import browserbase

    bb = browserbase.Browserbase(api_key=api_key or os.environ["BROWSERBASE_API_KEY"])
    kwargs = {}
    project_id = project_id or _env_str("BROWSERBASE_PROJECT_ID")
    if project_id:
        kwargs["project_id"] = project_id
    return bb.contexts.create(**kwargs).id


def upload_extension(zip_path: str, api_key: Optional[str] = None) -> str:
    """Uploads a Chrome extension (zip) to Browserbase and returns its id."""
    import browserbase

    bb = browserbase.Browserbase(api_key=api_key or os.environ["BROWSERBASE_API_KEY"])
    with open(zip_path, "rb") as f:
        return bb.extensions.create(file=f).id

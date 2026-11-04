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
"""Shared fixtures: a fake Gemini API server, so the real google-genai SDK
serializes requests and parses responses without network access or API keys."""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional

import pytest
from google import genai
from google.genai import types



class FakeGeminiServer:
    """Answers POSTs with scripted JSON responses and records the requests.

    Also serves static HTML pages registered in `pages` for browser tests.
    """

    def __init__(self):
        self.requests: list[dict[str, Any]] = []
        self.paths: list[str] = []
        self.responses: list[Any] = []
        self.pages: dict[str, bytes] = {}
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = server.pages.get(self.path.split("?")[0])
                if body is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                server.requests.append(body)
                server.paths.append(self.path)
                index = len(server.requests) - 1
                response = server.responses[min(index, len(server.responses) - 1)]
                if callable(response):
                    response = response(body)
                payload = json.dumps(response).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._httpd.server_port}"

    def client(self) -> genai.Client:
        return genai.Client(api_key="test-key", http_options=types.HttpOptions(base_url=self.url))

    def close(self):
        self._httpd.shutdown()


@pytest.fixture
def fake_gemini():
    server = FakeGeminiServer()
    yield server
    server.close()


def interaction(
    *,
    calls: Optional[list[tuple[str, dict]]] = None,
    text: Optional[str] = None,
    status: Optional[str] = None,
    id_: str = "interaction-1",
    thought: Optional[str] = None,
) -> dict:
    """Builds an Interactions API response body."""
    steps: list[dict] = []
    if thought:
        steps.append({"type": "thought", "signature": "c2ln", "summary": [{"type": "text", "text": thought}]})
    if text:
        steps.append({"type": "model_output", "content": [{"type": "text", "text": text}]})
    for i, (name, args) in enumerate(calls or []):
        steps.append({"type": "function_call", "id": f"{id_}-call-{i}", "name": name, "arguments": args})
    return {
        "id": id_,
        "status": status or ("requires_action" if calls else "completed"),
        "steps": steps,
        "usage": {"total_tokens": 10, "total_input_tokens": 8, "total_output_tokens": 2},
    }


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Keep tests independent from the developer's .env / shell."""
    for key in list(os.environ):
        if key.startswith(("BROWSERBASE_", "BROWSER_USE_", "GEMINI_", "GOOGLE_GENAI_")) or key in (
            "USE_VERTEXAI",
            "COMPUTER_USE_ENV",
            "COMPUTER_USE_ENGINE",
            "SCREEN_SIZE",
            "PLAYWRIGHT_HEADLESS",
            "CDP_URL",
            "MAX_STEPS",
            "SAFETY_CONFIRMATION",
            "RUN_LOG_DIR",
            "LIVE_VIEW_URL",
        ):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

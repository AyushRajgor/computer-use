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
"""Backends against a fake API server, through the real google-genai SDK."""
import base64
import json

import pytest

from backends import (
    BackendConfig,
    GenerateContentBackend,
    InteractionsBackend,
    ToolCall,
    ToolResult,
    call_with_retries,
    is_retryable,
)
from custom_functions import YIELD_TO_USER
from tests.conftest import interaction

PNG = b"\x89PNG\r\n\x1a\nfake"


def config(**kwargs) -> BackendConfig:
    defaults = dict(
        model="gemini-3.8-flash",
        enable_prompt_injection_detection=True,
        base_retry_delay_s=0,
    )
    defaults.update(kwargs)
    return BackendConfig(**defaults)


def result_for(turn, payload=None, screenshot=PNG):
    return [ToolResult(call=c, payload=payload or {"url": "https://x"}, screenshot=screenshot) for c in turn.calls]


class TestInteractionsServerHistory:
    def test_request_shape_and_chaining(self, fake_gemini):
        fake_gemini.responses = [
            interaction(calls=[("click", {"x": 1, "y": 2, "intent": "i"})], id_="i1", thought="hmm"),
            interaction(text="Done", id_="i2"),
        ]
        cfg = config(
            disabled_safety_policies=["data_modification"],
            excluded_predefined_functions=["drag_and_drop"],
            custom_functions=[YIELD_TO_USER],
            system_instruction="Be careful",
            thinking_level="low",
            media_resolution="high",
        )
        backend = InteractionsBackend(fake_gemini.client(), cfg, history="server")
        turn = backend.start("task", PNG)
        assert [c.name for c in turn.calls] == ["click"]
        assert turn.thoughts == "hmm"
        assert turn.calls[0].args["intent"] == "i"

        first = fake_gemini.requests[0]
        assert fake_gemini.paths[0].endswith("/interactions")
        assert first["model"] == "gemini-3.8-flash"
        assert first["system_instruction"] == "Be careful"
        assert first["generation_config"]["thinking_level"] == "low"
        cu, fn = first["tools"]
        assert cu == {
            "type": "computer_use",
            "environment": "browser",
            "enable_prompt_injection_detection": True,
            "excluded_predefined_functions": ["drag_and_drop"],
            "disabled_safety_policies": ["data_modification"],
        }
        assert fn["type"] == "function" and fn["name"] == "yield_to_user"
        (user_input,) = first["input"]
        text, image = user_input["content"]
        assert text == {"type": "text", "text": "task"}
        assert base64.b64decode(image["data"]) == PNG and image["resolution"] == "high"
        assert "store" not in first and "previous_interaction_id" not in first

        turn2 = backend.send_results(result_for(turn, {"url": "https://x", "safety_acknowledgement": True}))
        assert turn2.text == "Done" and not turn2.calls
        second = fake_gemini.requests[1]
        assert second["previous_interaction_id"] == "i1"
        assert second["tools"] == first["tools"]  # tools/config are re-sent every turn
        (fr,) = second["input"]
        assert fr["type"] == "function_result" and fr["name"] == "click" and fr["call_id"] == "i1-call-0"
        assert json.loads(fr["result"][0]["text"]) == {"url": "https://x", "safety_acknowledgement": True}
        assert fr["result"][1]["type"] == "image"

    def test_retry_resends_last_input(self, fake_gemini):
        fake_gemini.responses = [
            interaction(calls=[("click", {"x": 1, "y": 2})], id_="i1"),
            interaction(id_="i2", status="completed"),  # empty
            interaction(text="ok", id_="i3"),
        ]
        backend = InteractionsBackend(fake_gemini.client(), config())
        turn = backend.start("task", None)
        empty = backend.send_results(result_for(turn))
        assert empty.retryable_empty
        backend.retry()
        assert fake_gemini.requests[2]["previous_interaction_id"] == "i1"
        assert fake_gemini.requests[2]["input"] == fake_gemini.requests[1]["input"]

    def test_error_results_are_flagged(self, fake_gemini):
        fake_gemini.responses = [interaction(calls=[("click", {"x": 1, "y": 1})]), interaction(text="ok")]
        backend = InteractionsBackend(fake_gemini.client(), config())
        turn = backend.start("task", None)
        backend.send_results([ToolResult(call=turn.calls[0], payload={"error": "boom"}, is_error=True)])
        assert fake_gemini.requests[1]["input"][0]["is_error"] is True


class TestInteractionsClientHistory:
    def test_full_history_and_store_false(self, fake_gemini):
        fake_gemini.responses = [
            interaction(calls=[("click", {"x": 1, "y": 2})], id_="i1", thought="t"),
            interaction(text="Done", id_="i2"),
        ]
        backend = InteractionsBackend(fake_gemini.client(), config(), history="client")
        turn = backend.start("task", PNG)
        backend.send_results(result_for(turn))
        second = fake_gemini.requests[1]
        assert second["store"] is False and "previous_interaction_id" not in second
        types_ = [s["type"] for s in second["input"]]
        assert types_ == ["user_input", "thought", "function_call", "function_result"]
        assert second["input"][1]["signature"] == "c2ln"  # thought signatures are preserved

    def test_old_screenshots_are_trimmed(self, fake_gemini):
        fake_gemini.responses = [interaction(calls=[("click", {"x": 1, "y": 2})], id_=f"i{i}") for i in range(4)]
        backend = InteractionsBackend(fake_gemini.client(), config(max_recent_screenshots=2), history="client")
        turn = backend.start("task", None)
        for _ in range(3):
            turn = backend.send_results(result_for(turn))
        last = fake_gemini.requests[-1]["input"]
        results = [s for s in last if s["type"] == "function_result"]
        with_images = [any(c["type"] == "image" for c in s["result"]) for s in results]
        assert with_images == [False, True, True]
        assert all(s["result"][0]["type"] == "text" for s in results)


class TestInteractionsParse:
    def test_failed_status(self):
        turn = InteractionsBackend.parse(
            {"id": "x", "status": "failed", "steps": [], "errors": [{"message": "prompt injection detected"}]}
        )
        assert turn.error == "prompt injection detected" and not turn.retryable_empty

    def test_ignores_echoed_input_steps(self):
        turn = InteractionsBackend.parse(
            {
                "status": "requires_action",
                "steps": [
                    {"type": "function_call", "id": "old", "name": "click", "arguments": {}},
                    {"type": "function_result", "call_id": "old", "result": "x"},
                    {"type": "function_call", "id": "new", "name": "wait", "arguments": {}},
                ],
            }
        )
        assert [c.id for c in turn.calls] == ["new"]


class TestGenerateContent:
    def test_round_trip(self, fake_gemini):
        fake_gemini.responses = [
            {
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [
                                {"text": "thinking", "thought": True},
                                {"functionCall": {"id": "c1", "name": "type", "args": {"text": "hi"}}},
                            ],
                        },
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {"promptTokenCount": 5, "totalTokenCount": 9},
            },
            {"candidates": [{"content": {"role": "model", "parts": [{"text": "Done"}]}, "finishReason": "STOP"}]},
        ]
        cfg = config(environment="desktop", disabled_safety_policies=["data_modification"], thinking_level="low")
        backend = GenerateContentBackend(fake_gemini.client(), cfg)
        turn = backend.start("task", PNG)
        assert turn.calls == [ToolCall(name="type", args={"text": "hi"}, id="c1")]
        assert turn.thoughts == "thinking" and turn.usage["total_tokens"] == 9
        first = fake_gemini.requests[0]
        assert fake_gemini.paths[0].endswith(":generateContent")
        cu = first["tools"][0]["computerUse"]
        assert cu["environment"] == "ENVIRONMENT_DESKTOP"
        assert cu["disabled_safety_policies"] == ["DATA_MODIFICATION"]
        assert "temperature" not in first.get("generationConfig", {})

        turn2 = backend.send_results(result_for(turn, {"safety_acknowledgement": True}))
        assert turn2.text == "Done"
        fr = fake_gemini.requests[1]["contents"][-1]["parts"][0]["functionResponse"]
        assert fr["id"] == "c1" and fr["name"] == "type"
        assert fr["response"] == {"safety_acknowledgement": "true"}

    def test_malformed_function_call_is_retryable(self, fake_gemini):
        fake_gemini.responses = [
            {"candidates": [{"content": {"role": "model", "parts": []}, "finishReason": "MALFORMED_FUNCTION_CALL"}]}
        ]
        backend = GenerateContentBackend(fake_gemini.client(), config())
        assert backend.start("task", None).retryable_empty


class TestRetries:
    def test_retryable_codes(self):
        class E(Exception):
            def __init__(self, code):
                self.status_code = code

        assert is_retryable(E(429)) and is_retryable(E(503))
        assert not is_retryable(E(400)) and not is_retryable(E(404))

    def test_call_with_retries(self):
        attempts = []

        class Flaky(Exception):
            status_code = 503

        def fn():
            attempts.append(1)
            if len(attempts) < 3:
                raise Flaky()
            return "ok"

        assert call_with_retries(fn, max_retries=5, base_delay_s=0) == "ok"
        assert len(attempts) == 3

    def test_non_retryable_raises_immediately(self):
        class Bad(Exception):
            status_code = 400

        attempts = []

        def fn():
            attempts.append(1)
            raise Bad()

        with pytest.raises(Bad):
            call_with_retries(fn, max_retries=5, base_delay_s=0)
        assert len(attempts) == 1

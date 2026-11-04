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
"""Model backends for the Computer Use agent loop.

Two backends implement the same small interface:

* `InteractionsBackend` -- the Interactions API (`client.interactions.create`),
  the recommended API for Gemini. Supports server-side history
  (`previous_interaction_id`, default) or client-side history (`store=False`).
* `GenerateContentBackend` -- the legacy `client.models.generate_content` API.

The agent only deals with `ModelTurn` / `ToolCall` / `ToolResult`, so the loop
logic is identical for both APIs.
"""
import abc
import base64
import dataclasses
import json
import logging
import time
from typing import Any, Callable, Optional, Sequence

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

# HTTP status codes worth retrying.
_RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


@dataclasses.dataclass
class ToolCall:
    """A function call requested by the model."""

    name: str
    args: dict[str, Any]
    id: Optional[str] = None


@dataclasses.dataclass
class ToolResult:
    """The result of executing a `ToolCall`, sent back to the model."""

    call: ToolCall
    payload: dict[str, Any]
    screenshot: Optional[bytes] = None
    is_error: bool = False


@dataclasses.dataclass
class ModelTurn:
    """One model response, normalized across APIs."""

    text: Optional[str] = None
    thoughts: Optional[str] = None
    calls: list[ToolCall] = dataclasses.field(default_factory=list)
    status: Optional[str] = None
    # True if the response should be retried (e.g. malformed function call).
    retryable_empty: bool = False
    error: Optional[str] = None
    usage: dict[str, int] = dataclasses.field(default_factory=dict)
    raw: Any = None


@dataclasses.dataclass
class BackendConfig:
    model: str
    environment: str = "browser"
    excluded_predefined_functions: Sequence[str] = ()
    disabled_safety_policies: Sequence[str] = ()
    enable_prompt_injection_detection: Optional[bool] = None
    custom_functions: Sequence[Any] = ()  # custom_functions.CustomFunction
    system_instruction: Optional[str] = None
    thinking_level: Optional[str] = None
    media_resolution: Optional[str] = None
    # Number of most recent turns whose screenshots are kept in the history
    # (client-side history only). 0 = keep all.
    max_recent_screenshots: int = 3
    max_retries: int = 5
    base_retry_delay_s: float = 1.0


def _status_code(exc: BaseException) -> Optional[int]:
    for attr in ("status_code", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    return None


def is_retryable(exc: BaseException) -> bool:
    code = _status_code(exc)
    if code is not None:
        return code in _RETRYABLE_STATUS
    # Network level failures (timeouts, resets, ...) carry no status code.
    name = type(exc).__name__.lower()
    return any(
        token in name
        for token in ("timeout", "connect", "network", "noresponse", "remoteprotocol")
    )


def call_with_retries(
    fn: Callable[[], Any],
    *,
    max_retries: int,
    base_delay_s: float,
    on_retry: Optional[Callable[[int, float, BaseException], None]] = None,
) -> Any:
    for attempt in range(max_retries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - re-raised below
            if attempt >= max_retries - 1 or not is_retryable(e):
                raise
            delay = base_delay_s * (2**attempt)
            if on_retry:
                on_retry(attempt + 1, delay, e)
            time.sleep(delay)
    raise RuntimeError("unreachable")


class ModelBackend(abc.ABC):
    def __init__(
        self,
        client: genai.Client,
        config: BackendConfig,
        on_retry: Optional[Callable[[int, float, BaseException], None]] = None,
    ):
        self._client = client
        self._config = config
        self._on_retry = on_retry

    @abc.abstractmethod
    def start(self, query: str, screenshot: Optional[bytes]) -> ModelTurn:
        """Sends the initial user query (and optional screenshot)."""

    @abc.abstractmethod
    def send_results(self, results: Sequence[ToolResult]) -> ModelTurn:
        """Sends function results for the previous turn's calls."""

    @abc.abstractmethod
    def retry(self) -> ModelTurn:
        """Re-sends the last request (e.g. after a malformed function call)."""

    def _call(self, fn: Callable[[], Any]) -> Any:
        return call_with_retries(
            fn,
            max_retries=self._config.max_retries,
            base_delay_s=self._config.base_retry_delay_s,
            on_retry=self._on_retry,
        )


# ---------------------------------------------------------------------------
# Interactions API
# ---------------------------------------------------------------------------


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("utf-8")


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Reads `key` from a pydantic model or a dict."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


class InteractionsBackend(ModelBackend):
    """Backend for `client.interactions.create` (Interactions API)."""

    def __init__(self, *args, history: str = "server", **kwargs):
        super().__init__(*args, **kwargs)
        if history not in ("server", "client"):
            raise ValueError(f"Unknown history mode: {history}")
        self._history_mode = history
        self._previous_interaction_id: Optional[str] = None
        self._history: list[dict[str, Any]] = []  # client-side history only
        self._history_len_before_output = 0
        self._last_input: Any = None
        self._last_previous_id: Optional[str] = None

    # -- request building ---------------------------------------------------

    def tools(self) -> list[dict[str, Any]]:
        cfg = self._config
        computer_use: dict[str, Any] = {
            "type": "computer_use",
            "environment": cfg.environment,
        }
        if cfg.enable_prompt_injection_detection is not None:
            computer_use["enable_prompt_injection_detection"] = bool(
                cfg.enable_prompt_injection_detection
            )
        if cfg.excluded_predefined_functions:
            computer_use["excluded_predefined_functions"] = list(
                cfg.excluded_predefined_functions
            )
        if cfg.disabled_safety_policies:
            computer_use["disabled_safety_policies"] = [
                p.lower() for p in cfg.disabled_safety_policies
            ]
        tools = [computer_use]
        tools.extend(f.to_interactions_tool() for f in cfg.custom_functions)
        return tools

    def _image(self, data: bytes) -> dict[str, Any]:
        image: dict[str, Any] = {
            "type": "image",
            "data": _b64(data),
            "mime_type": "image/png",
        }
        if self._config.media_resolution:
            image["resolution"] = self._config.media_resolution
        return image

    def _request_kwargs(self) -> dict[str, Any]:
        cfg = self._config
        kwargs: dict[str, Any] = {"model": cfg.model, "tools": self.tools()}
        if cfg.system_instruction:
            kwargs["system_instruction"] = cfg.system_instruction
        if cfg.thinking_level:
            kwargs["generation_config"] = {
                "thinking_level": cfg.thinking_level,
                "thinking_summaries": "auto",
            }
        if self._history_mode == "client":
            kwargs["store"] = False
        return kwargs

    def _function_result(self, result: ToolResult) -> dict[str, Any]:
        content: list[dict[str, Any]] = [
            {"type": "text", "text": json.dumps(result.payload, default=str)}
        ]
        if result.screenshot:
            content.append(self._image(result.screenshot))
        step: dict[str, Any] = {
            "type": "function_result",
            "name": result.call.name,
            "call_id": result.call.id or "",
            "result": content,
        }
        if result.is_error:
            step["is_error"] = True
        return step

    # -- sending ------------------------------------------------------------

    def start(self, query: str, screenshot: Optional[bytes]) -> ModelTurn:
        content: list[dict[str, Any]] = [{"type": "text", "text": query}]
        if screenshot:
            content.append(self._image(screenshot))
        if self._history_mode == "client":
            self._history = [{"type": "user_input", "content": content}]
            return self._send(None)
        return self._send(content)

    def send_results(self, results: Sequence[ToolResult]) -> ModelTurn:
        steps = [self._function_result(r) for r in results]
        if self._history_mode == "client":
            self._history.extend(steps)
            self._trim_screenshots()
            return self._send(None)
        return self._send(steps)

    def retry(self) -> ModelTurn:
        if self._history_mode == "client":
            # Drop the empty/malformed model output before asking again.
            del self._history[self._history_len_before_output :]
            return self._send(None)
        return self._send(self._last_input, previous_id=self._last_previous_id)

    def _send(self, input_: Any, previous_id: Any = ...) -> ModelTurn:
        kwargs = self._request_kwargs()
        if self._history_mode == "client":
            kwargs["input"] = list(self._history)
        else:
            if previous_id is ...:
                previous_id = self._previous_interaction_id
            self._last_input, self._last_previous_id = input_, previous_id
            kwargs["input"] = input_
            if previous_id:
                kwargs["previous_interaction_id"] = previous_id

        interaction = self._call(lambda: self._client.interactions.create(**kwargs))
        turn = self.parse(interaction)
        if self._history_mode == "client":
            self._history_len_before_output = len(self._history)
            self._history.extend(self._output_steps_as_dicts(interaction))
        elif _get(interaction, "id"):
            self._previous_interaction_id = _get(interaction, "id")
        return turn

    # -- response parsing ---------------------------------------------------

    @staticmethod
    def _output_steps(interaction: Any) -> list[Any]:
        steps = list(_get(interaction, "steps") or [])
        # Defensive: only look at steps produced after the last input step.
        last_input = -1
        for i, step in enumerate(steps):
            if _get(step, "type") in ("user_input", "function_result"):
                last_input = i
        return steps[last_input + 1 :]

    def _output_steps_as_dicts(self, interaction: Any) -> list[dict[str, Any]]:
        out = []
        for step in self._output_steps(interaction):
            if isinstance(step, dict):
                out.append(step)
            else:
                out.append(step.model_dump(mode="json", by_alias=True, exclude_none=True))
        return out

    @classmethod
    def parse(cls, interaction: Any) -> ModelTurn:
        texts: list[str] = []
        thoughts: list[str] = []
        calls: list[ToolCall] = []
        errors: list[str] = []
        for step in cls._output_steps(interaction):
            step_type = _get(step, "type")
            if step_type == "function_call":
                calls.append(
                    ToolCall(
                        name=_get(step, "name"),
                        args=dict(_get(step, "arguments") or {}),
                        id=_get(step, "id"),
                    )
                )
            elif step_type == "model_output":
                for block in _get(step, "content") or []:
                    if _get(block, "type") == "text" and _get(block, "text"):
                        texts.append(_get(block, "text"))
                err = _get(step, "error")
                if err:
                    errors.append(str(_get(err, "message") or err))
            elif step_type == "thought":
                for block in _get(step, "summary") or []:
                    if _get(block, "text"):
                        thoughts.append(_get(block, "text"))

        status = _get(interaction, "status")
        for err in _get(interaction, "errors") or []:
            errors.append(str(_get(err, "message") or err))

        usage = {}
        raw_usage = _get(interaction, "usage")
        if raw_usage is not None:
            for key in (
                "total_input_tokens",
                "total_output_tokens",
                "total_thought_tokens",
                "total_cached_tokens",
                "total_tokens",
            ):
                value = _get(raw_usage, key)
                if isinstance(value, int):
                    usage[key] = value

        text = "\n".join(texts).strip() or None
        failed = status in ("failed", "cancelled", "budget_exceeded")
        return ModelTurn(
            text=text,
            thoughts="\n".join(thoughts).strip() or None,
            calls=calls,
            status=status,
            retryable_empty=not calls and not text and not failed,
            error="; ".join(errors) or (f"Interaction {status}" if failed else None),
            usage=usage,
            raw=interaction,
        )

    def _trim_screenshots(self) -> None:
        """Drops images from old function results (client-side history only)."""
        keep = self._config.max_recent_screenshots
        if keep <= 0:
            return
        seen_turns = 0
        # Walk backwards over groups of consecutive function_result steps.
        i = len(self._history) - 1
        while i >= 0:
            if self._history[i].get("type") != "function_result":
                i -= 1
                continue
            group_end = i
            while i >= 0 and self._history[i].get("type") == "function_result":
                i -= 1
            group = self._history[i + 1 : group_end + 1]
            if any(
                isinstance(c, dict) and c.get("type") == "image"
                for step in group
                for c in step.get("result", [])
            ):
                seen_turns += 1
                if seen_turns > keep:
                    for step in group:
                        step["result"] = [
                            c
                            for c in step.get("result", [])
                            if not (isinstance(c, dict) and c.get("type") == "image")
                        ]


# ---------------------------------------------------------------------------
# generateContent API (legacy)
# ---------------------------------------------------------------------------

_ENVIRONMENTS = {
    "browser": types.Environment.ENVIRONMENT_BROWSER,
    "desktop": types.Environment.ENVIRONMENT_DESKTOP,
    "mobile": types.Environment.ENVIRONMENT_MOBILE,
}


class GenerateContentBackend(ModelBackend):
    """Backend for the legacy `client.models.generate_content` API."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._contents: list[types.Content] = []
        self._generate_content_config = self._build_config()

    def _build_config(self) -> types.GenerateContentConfig:
        cfg = self._config
        computer_use_kwargs: dict[str, Any] = {
            "environment": _ENVIRONMENTS[cfg.environment],
            "excluded_predefined_functions": list(cfg.excluded_predefined_functions),
        }
        if cfg.enable_prompt_injection_detection is not None:
            computer_use_kwargs["enable_prompt_injection_detection"] = bool(
                cfg.enable_prompt_injection_detection
            )
        if cfg.disabled_safety_policies:
            computer_use_kwargs["disabled_safety_policies"] = [
                types.SafetyPolicy(p.upper()) for p in cfg.disabled_safety_policies
            ]
        tools = [types.Tool(computer_use=types.ComputerUse(**computer_use_kwargs))]
        if cfg.custom_functions:
            tools.append(
                types.Tool(
                    function_declarations=[
                        f.to_function_declaration() for f in cfg.custom_functions
                    ]
                )
            )
        thinking = types.ThinkingConfig(include_thoughts=True)
        if cfg.thinking_level:
            thinking.thinking_level = types.ThinkingLevel(cfg.thinking_level.upper())
        config = types.GenerateContentConfig(
            tools=tools,
            thinking_config=thinking,
            system_instruction=cfg.system_instruction or None,
            # Function calls are executed by the agent loop, not by the SDK.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True
            ),
        )
        if cfg.media_resolution:
            config.media_resolution = {
                "low": types.MediaResolution.MEDIA_RESOLUTION_LOW,
                "medium": types.MediaResolution.MEDIA_RESOLUTION_MEDIUM,
                "high": types.MediaResolution.MEDIA_RESOLUTION_HIGH,
                "ultra_high": types.MediaResolution.MEDIA_RESOLUTION_HIGH,
            }[cfg.media_resolution]
        return config

    def start(self, query: str, screenshot: Optional[bytes]) -> ModelTurn:
        parts = [types.Part(text=query)]
        if screenshot:
            parts.append(types.Part.from_bytes(data=screenshot, mime_type="image/png"))
        self._contents = [types.Content(role="user", parts=parts)]
        return self._send()

    def send_results(self, results: Sequence[ToolResult]) -> ModelTurn:
        parts = []
        for r in results:
            fr_parts = None
            if r.screenshot:
                fr_parts = [
                    types.FunctionResponsePart(
                        inline_data=types.FunctionResponseBlob(
                            mime_type="image/png", data=r.screenshot
                        )
                    )
                ]
            payload = dict(r.payload)
            if payload.get("safety_acknowledgement") is True:
                # generateContent expects the acknowledgement as a string.
                payload["safety_acknowledgement"] = "true"
            if r.is_error and "error" not in payload:
                payload["error"] = "Function execution failed."
            parts.append(
                types.Part(
                    function_response=types.FunctionResponse(
                        id=r.call.id,
                        name=r.call.name,
                        response=payload,
                        parts=fr_parts,
                    )
                )
            )
        self._contents.append(types.Content(role="user", parts=parts))
        self._trim_screenshots()
        return self._send()

    def retry(self) -> ModelTurn:
        # Drop the (empty / malformed) model turn if it was appended.
        if self._contents and self._contents[-1].role == "model":
            self._contents.pop()
        return self._send()

    def _send(self) -> ModelTurn:
        response = self._call(
            lambda: self._client.models.generate_content(
                model=self._config.model,
                contents=self._contents,
                config=self._generate_content_config,
            )
        )
        turn = self.parse(response)
        if response.candidates and response.candidates[0].content:
            self._contents.append(response.candidates[0].content)
        return turn

    @staticmethod
    def parse(response: types.GenerateContentResponse) -> ModelTurn:
        if not response.candidates:
            feedback = response.prompt_feedback
            reason = feedback.block_reason if feedback else None
            return ModelTurn(error=f"Empty response (block reason: {reason})", raw=response)
        candidate = response.candidates[0]
        texts, thoughts, calls = [], [], []
        if candidate.content and candidate.content.parts:
            for part in candidate.content.parts:
                if part.function_call:
                    calls.append(
                        ToolCall(
                            name=part.function_call.name,
                            args=dict(part.function_call.args or {}),
                            id=part.function_call.id,
                        )
                    )
                elif part.text:
                    (thoughts if part.thought else texts).append(part.text)
        usage = {}
        if response.usage_metadata:
            um = response.usage_metadata
            for key, attr in (
                ("total_input_tokens", "prompt_token_count"),
                ("total_output_tokens", "candidates_token_count"),
                ("total_thought_tokens", "thoughts_token_count"),
                ("total_cached_tokens", "cached_content_token_count"),
                ("total_tokens", "total_token_count"),
            ):
                value = getattr(um, attr, None)
                if isinstance(value, int):
                    usage[key] = value
        text = " ".join(texts).strip() or None
        malformed = candidate.finish_reason == types.FinishReason.MALFORMED_FUNCTION_CALL
        return ModelTurn(
            text=text,
            thoughts="\n".join(thoughts).strip() or None,
            calls=calls,
            status=str(candidate.finish_reason) if candidate.finish_reason else None,
            retryable_empty=not calls and not text and malformed,
            usage=usage,
            raw=response,
        )

    def _trim_screenshots(self) -> None:
        keep = self._config.max_recent_screenshots
        if keep <= 0:
            return
        seen = 0
        for content in reversed(self._contents):
            if content.role != "user" or not content.parts:
                continue
            with_images = [
                p
                for p in content.parts
                if p.function_response and p.function_response.parts
            ]
            if not with_images:
                continue
            seen += 1
            if seen > keep:
                for part in with_images:
                    part.function_response.parts = None

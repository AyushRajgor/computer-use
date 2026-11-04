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
"""Model registry and Computer Use action names.

Sources (checked November 2026):
  * https://aistudio.google.com/docs/computer-use (model versions, actions)
  * https://aistudio.google.com/docs/deprecations
  * https://aistudio.google.com/docs/changelog (Computer Use GA, Oct 30 2026)
"""
import dataclasses
from typing import Literal, Optional

ModelStatus = Literal["recommended", "stable", "preview", "deprecated", "shutdown"]

DEFAULT_MODEL = "gemini-3.8-flash"


@dataclasses.dataclass(frozen=True)
class ModelInfo:
    name: str
    status: ModelStatus
    description: str
    # Prompt injection detection is supported on Gemini 3.5 Flash-Lite or later.
    supports_prompt_injection_detection: bool = True
    # Thinking levels accepted by the model (None = not validated client-side).
    thinking_levels: Optional[tuple[str, ...]] = None
    # The model the API routes to / the recommended replacement.
    replacement: Optional[str] = None
    note: Optional[str] = None


MODELS: dict[str, ModelInfo] = {
    m.name: m
    for m in [
        ModelInfo(
            name="gemini-3.8-flash",
            status="recommended",
            description="Recommended model for computer use: high-accuracy UI "
            "interaction and reliable tool calling.",
            thinking_levels=("low", "medium", "high"),
        ),
        ModelInfo(
            name="gemini-4-argon",
            status="stable",
            description="Most capable model, for complex reasoning and agentic "
            "UI workflows.",
        ),
        ModelInfo(
            name="gemini-3.6-flash",
            status="stable",
            description="Previous Flash generation with computer use support.",
        ),
        ModelInfo(
            name="gemini-3.5-flash-lite",
            status="stable",
            description="Low-latency, cost-effective model supporting computer use.",
        ),
        ModelInfo(
            name="gemini-3-flash-preview",
            status="preview",
            description="Preview model supporting computer use.",
            supports_prompt_injection_detection=False,
        ),
        ModelInfo(
            name="gemini-3.1-pro-preview",
            status="preview",
            description="Preview model (legacy action set). Not listed in the GA "
            "computer use docs.",
            supports_prompt_injection_detection=False,
        ),
        ModelInfo(
            name="gemini-3.7-flash",
            status="deprecated",
            description="Deprecated; API requests are routed to gemini-3.8-flash.",
            replacement="gemini-3.8-flash",
        ),
        ModelInfo(
            name="gemini-3.5-flash",
            status="deprecated",
            description="Deprecated; API requests are routed to gemini-3.6-flash.",
            replacement="gemini-3.6-flash",
        ),
        ModelInfo(
            name="gemini-2.5-computer-use-preview-10-2025",
            status="shutdown",
            description="Shut down on July 28, 2026.",
            supports_prompt_injection_detection=False,
            replacement="gemini-3.8-flash",
        ),
    ]
}


def get_model_info(model_name: str) -> Optional[ModelInfo]:
    # Accept fully qualified names such as "models/gemini-3.8-flash" or
    # Vertex/Agent Platform resource names ending with the model id.
    return MODELS.get(model_name.rsplit("/", 1)[-1])


# ---------------------------------------------------------------------------
# Predefined Computer Use functions
# ---------------------------------------------------------------------------

# Browser environment (Gemini 4 and Gemini 3.x).
BROWSER_FUNCTIONS = (
    "click",
    "double_click",
    "triple_click",
    "middle_click",
    "right_click",
    "mouse_down",
    "mouse_up",
    "move",
    "type",
    "drag_and_drop",
    "wait",
    "press_key",
    "key_down",
    "key_up",
    "hotkey",
    "take_screenshot",
    "scroll",
    "go_back",
    "navigate",
    "go_forward",
)

# Desktop environment: the browser set without the navigation actions.
DESKTOP_FUNCTIONS = tuple(
    f for f in BROWSER_FUNCTIONS if f not in ("go_back", "navigate", "go_forward")
)

# Mobile environment (not implemented by this repository's computers, listed
# for completeness / exclusion lists).
MOBILE_FUNCTIONS = (
    "open_app",
    "click",
    "list_apps",
    "wait",
    "go_back",
    "type",
    "drag_and_drop",
    "long_press",
    "press_key",
    "take_screenshot",
)

# Legacy action set used by gemini-2.5-computer-use-preview-10-2025 and some
# preview models (gemini-3-flash-preview, gemini-3.1-pro-preview).
LEGACY_FUNCTIONS = (
    "open_web_browser",
    "click_at",
    "hover_at",
    "type_text_at",
    "scroll_document",
    "scroll_at",
    "wait_5_seconds",
    "go_back",
    "go_forward",
    "search",
    "navigate",
    "key_combination",
    "drag_and_drop",
)

ALL_PREDEFINED_FUNCTIONS = frozenset(
    BROWSER_FUNCTIONS + DESKTOP_FUNCTIONS + MOBILE_FUNCTIONS + LEGACY_FUNCTIONS
)

# Safety policy categories that can be disabled (as preferences) per request.
SAFETY_POLICIES = (
    "financial_transactions",
    "sensitive_data_modification",
    "communication_tool",
    "account_creation",
    "data_modification",
    "user_consent_management",
    "legal_terms_and_agreements",
)

THINKING_LEVELS = ("minimal", "low", "medium", "high")
MEDIA_RESOLUTIONS = ("low", "medium", "high", "ultra_high")

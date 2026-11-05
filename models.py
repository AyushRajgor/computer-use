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
  * https://aistudio.google.com/docs/pricing (prices, checked Nov 5 2026)
"""
import dataclasses
import datetime
from typing import Any, Literal, Optional

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
# Prices: Gemini Developer API, paid tier, Standard (not Batch, Flex or
# Priority), USD per 1M tokens. The free tier costs nothing. Checked
# November 5, 2026 against https://aistudio.google.com/docs/pricing
# ---------------------------------------------------------------------------

PRICES_URL = "https://aistudio.google.com/docs/pricing"


@dataclasses.dataclass(frozen=True)
class Rates:
    input: float
    output: float  # thinking tokens are billed as output
    cached: float  # cached input (context caching); storage is not counted


@dataclasses.dataclass(frozen=True)
class ModelPrice:
    rates: Rates
    since: Optional[datetime.date] = None  # the first day these rates apply
    # Requests whose prompt is longer than this are billed at `long_rates`.
    long_prompt: Optional[int] = None
    long_rates: Optional[Rates] = None


_FLASH_2026 = Rates(input=0.75, output=3.75, cached=0.075)
_FLASH_2027 = Rates(input=1.50, output=7.50, cached=0.15)

# Oldest first. Deprecated models are billed at their replacement's rates.
PRICES: dict[str, tuple[ModelPrice, ...]] = {
    "gemini-4-argon": (
        ModelPrice(Rates(input=2.00, output=10.00, cached=0.10)),
        ModelPrice(Rates(input=4.00, output=20.00, cached=0.20), since=datetime.date(2026, 12, 1)),
    ),
    "gemini-3.8-flash": (ModelPrice(_FLASH_2026), ModelPrice(_FLASH_2027, since=datetime.date(2027, 1, 1))),
    "gemini-3.6-flash": (ModelPrice(_FLASH_2026), ModelPrice(_FLASH_2027, since=datetime.date(2027, 1, 1))),
    "gemini-3.5-flash-lite": (ModelPrice(Rates(input=0.30, output=2.50, cached=0.03)),),
    "gemini-3-flash-preview": (ModelPrice(Rates(input=0.50, output=3.00, cached=0.05)),),
    "gemini-3.1-pro-preview": (
        ModelPrice(
            Rates(input=2.00, output=12.00, cached=0.20),
            long_prompt=200_000,
            long_rates=Rates(input=4.00, output=18.00, cached=0.40),
        ),
    ),
}


@dataclasses.dataclass(frozen=True)
class Cost:
    """What a request (or a run, added up) costs in USD."""

    input: float = 0.0  # input that wasn't cached
    cached: float = 0.0  # cached input
    output: float = 0.0  # output and thinking

    @property
    def total(self) -> float:
        return self.input + self.cached + self.output

    def __add__(self, other: "Cost") -> "Cost":
        return Cost(self.input + other.input, self.cached + other.cached, self.output + other.output)


def priced_model(model_name: str) -> Optional[str]:
    """The model whose rates a request to `model_name` is billed at, or None
    when there's no price for it (a model routed elsewhere is billed there)."""
    name = model_name.rsplit("/", 1)[-1]
    info = MODELS.get(name)
    if name not in PRICES and info is not None and info.status == "deprecated" and info.replacement:
        name = info.replacement
    return name if name in PRICES else None


def model_price(model_name: str, day: Optional[datetime.date] = None) -> Optional[ModelPrice]:
    """The rates in force on `day` (today if None)."""
    name = priced_model(model_name)
    if name is None:
        return None
    day = day or datetime.date.today()
    current = PRICES[name][0]
    for price in PRICES[name]:
        if price.since is None or price.since <= day:
            current = price
    return current


def estimate_cost(
    model_name: str, usage: dict[str, Any], day: Optional[datetime.date] = None
) -> Optional[Cost]:
    """The cost of one request from its token counts (the `usage` a step logs).
    The long-prompt rates depend on each request's own prompt, so add up the
    requests rather than pricing a run's totals. None without a price or tokens."""
    price = model_price(model_name, day)

    def count(key: str) -> int:
        value = usage.get(key)
        return int(value) if isinstance(value, (int, float)) and value > 0 else 0

    prompt = count("total_input_tokens")
    output = count("total_output_tokens") + count("total_thought_tokens")
    if price is None or not (prompt or output):
        return None
    cached = min(count("total_cached_tokens"), prompt)
    rates = price.rates
    if price.long_prompt is not None and price.long_rates is not None and prompt > price.long_prompt:
        rates = price.long_rates
    return Cost(
        input=(prompt - cached) * rates.input / 1e6,
        cached=cached * rates.cached / 1e6,
        output=output * rates.output / 1e6,
    )


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

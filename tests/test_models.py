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
"""Model prices and cost estimates (models.py)."""
import datetime

import pytest

from models import MODELS, PRICES, Cost, estimate_cost, priced_model

NOV_2026 = datetime.date(2026, 11, 5)
USAGE = {  # a real 15-step run on gemini-3.8-flash
    "total_input_tokens": 181_069,
    "total_cached_tokens": 112_089,
    "total_output_tokens": 445,
    "total_thought_tokens": 658,
    "total_tokens": 182_172,
}


def test_cached_input_and_thinking_are_priced_separately():
    cost = estimate_cost("gemini-3.8-flash", USAGE, NOV_2026)
    assert cost.input == pytest.approx((181_069 - 112_089) * 0.75 / 1e6)
    assert cost.cached == pytest.approx(112_089 * 0.075 / 1e6)
    assert cost.output == pytest.approx((445 + 658) * 3.75 / 1e6)  # thinking is billed as output
    assert cost.total == pytest.approx(0.064278, abs=1e-6)


def test_rates_follow_the_run_date():
    before = estimate_cost("gemini-3.8-flash", USAGE, datetime.date(2026, 12, 31))
    after = estimate_cost("gemini-3.8-flash", USAGE, datetime.date(2027, 1, 1))
    assert after.total == pytest.approx(before.total * 2)
    argon_nov = estimate_cost("gemini-4-argon", USAGE, datetime.date(2026, 11, 30))
    argon_dec = estimate_cost("gemini-4-argon", USAGE, datetime.date(2026, 12, 1))
    assert argon_dec.total == pytest.approx(argon_nov.total * 2)


def test_long_prompts_cost_more_on_tiered_models():
    short = estimate_cost("gemini-3.1-pro-preview", {"total_input_tokens": 200_000}, NOV_2026)
    long = estimate_cost("gemini-3.1-pro-preview", {"total_input_tokens": 200_001}, NOV_2026)
    assert short.input == pytest.approx(200_000 * 2.00 / 1e6)
    assert long.input == pytest.approx(200_001 * 4.00 / 1e6)


def test_names_and_routed_models():
    assert priced_model("models/gemini-3.8-flash") == "gemini-3.8-flash"
    assert priced_model("gemini-3.7-flash") == "gemini-3.8-flash"  # deprecated: billed as its replacement
    assert priced_model("gemini-2.5-computer-use-preview-10-2025") is None  # shut down, not routed
    assert priced_model("not-a-model") is None
    assert estimate_cost("not-a-model", USAGE, NOV_2026) is None
    assert estimate_cost("gemini-3.8-flash", {}, NOV_2026) is None
    assert estimate_cost("gemini-3.8-flash", {"total_tokens": 100}, NOV_2026) is None  # no split: no price


def test_every_usable_model_has_a_price():
    for name, info in MODELS.items():
        if info.status in ("recommended", "stable", "preview"):
            assert name in PRICES, name


def test_costs_add_up():
    assert (Cost(1, 2, 3) + Cost(0.5, 0.5, 0.5)).total == pytest.approx(7.5)

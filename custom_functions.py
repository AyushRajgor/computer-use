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
"""Custom user-defined functions that can be offered to the model next to the
predefined Computer Use actions.

See https://aistudio.google.com/docs/computer-use#custom-functions

A custom function is a name, a description, a JSON schema for its parameters
and a Python handler. The handler receives the call arguments and returns a
JSON-serialisable dict that is sent back to the model as the function result.

Example -- add your own function:

    from custom_functions import CustomFunction

    def lookup_order(args: dict) -> dict:
        return {"status": "shipped", "order_id": args["order_id"]}

    LOOKUP_ORDER = CustomFunction(
        name="lookup_order",
        description="Looks up the shipping status of an order by id.",
        parameters={
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
        },
        handler=lookup_order,
    )

    agent = ComputerUseAgent(..., custom_functions=[LOOKUP_ORDER])
"""
import dataclasses
import sys
from typing import Any, Callable, Optional

from google.genai import types


@dataclasses.dataclass(frozen=True)
class CustomFunction:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[dict[str, Any]], dict[str, Any]]
    # If True, the agent also attaches a fresh screenshot to the result.
    returns_screenshot: bool = False

    def to_interactions_tool(self) -> dict[str, Any]:
        """Declaration for the Interactions API (`tools=[...]`)."""
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }

    def to_function_declaration(self) -> types.FunctionDeclaration:
        """Declaration for the legacy generateContent API."""
        return types.FunctionDeclaration(
            name=self.name,
            description=self.description,
            parameters_json_schema=self.parameters,
        )

    def __call__(self, args: dict[str, Any]) -> dict[str, Any]:
        result = self.handler(args)
        if not isinstance(result, dict):
            result = {"result": result}
        return result


# ---------------------------------------------------------------------------
# Built-in: yield_to_user (human in the loop)
# ---------------------------------------------------------------------------


def _ask_user(args: dict[str, Any], input_fn: Callable[[str], str] = input) -> dict:
    reason = args.get("reason", "The agent needs your help.")
    if not sys.stdin or not sys.stdin.isatty():
        return {
            "user_response": None,
            "error": "No interactive user is available. Continue without user "
            "input or finish the task and explain what is needed.",
        }
    print(f"\n[Agent needs your input] {reason}")
    print(
        "Type a reply for the agent (e.g. a 2FA code), or just press ENTER once "
        "you have finished doing it yourself in the browser:"
    )
    try:
        reply = input_fn("> ")
    except EOFError:
        reply = ""
    return {"user_response": reply or "The user has completed the requested step."}


YIELD_TO_USER = CustomFunction(
    name="yield_to_user",
    description=(
        "Yields control back to the user for assistance or verification when an "
        "automated action is unsafe or ambiguous, or when information only the "
        "user has is required (for example a one-time 2FA code, a password, a "
        "CAPTCHA, or a decision between options). Returns the user's reply."
    ),
    parameters={
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "The reason why the agent is yielding control to the human.",
            }
        },
        "required": ["reason"],
    },
    handler=_ask_user,
    returns_screenshot=True,
)

BUILTIN_FUNCTIONS: dict[str, CustomFunction] = {
    YIELD_TO_USER.name: YIELD_TO_USER,
}


def get_builtin(name: str) -> Optional[CustomFunction]:
    return BUILTIN_FUNCTIONS.get(name)

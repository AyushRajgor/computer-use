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
"""Optional alternative agent engines.

The default engine is this repository's Gemini Computer Use agent (agent.py).
Engines in this package run a task end-to-end with a different open-source
agent framework, still powered by Gemini. They are optional and need extra
dependencies (see requirements-engines.txt).
"""

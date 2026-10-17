"""Deterministic fallbacks, one per prompt name.

`llm.client` uses these when Ollama is not configured, unreachable, over the per-run call cap, or
returns output that fails validation twice. A template must never fail: it builds its output only
from the variables it is given. `llm.client` refuses to import if a prompt file in `prompts/` has no
template registered here.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from tracelayer_agents.schemas import Contract

TemplateFn = Callable[[dict[str, Any]], BaseModel]

TEMPLATES: dict[str, TemplateFn] = {}


def template(prompt_name: str) -> Callable[[TemplateFn], TemplateFn]:
    """Register `fn` as the deterministic fallback for `prompt_name` (e.g. "narrative")."""

    def register(fn: TemplateFn) -> TemplateFn:
        if prompt_name in TEMPLATES:
            raise ValueError(f"template for {prompt_name!r} already registered")
        TEMPLATES[prompt_name] = fn
        return fn

    return register


# --------------------------------------------------------------------------- echo (tests only)


class EchoOutput(Contract):
    text: str


@template("echo")
def echo(variables: dict[str, Any]) -> EchoOutput:
    return EchoOutput(text=str(variables.get("message", "")))

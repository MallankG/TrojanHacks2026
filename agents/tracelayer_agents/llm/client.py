"""Structured LLM calls with graceful degradation (CLAUDE.md rule 8).

Chain: Ollama → deterministic template. The demo works with no network and Ollama not running:
- `LLM_PROVIDER=template` skips Ollama entirely.
- Ollama unreachable / timeout / HTTP error / model not found / no local models → template.
- `LLM_TIMEOUT_S` bounds the whole Ollama phase of one call (health check, every attempt and the
  validation retry), not each request, so a slow model always reaches the template in time. LLM
  nodes run under `AgentConfig.llm_node_timeout_s`, which must be larger.
- Output that fails `output_model` validation is retried once with the error appended to the
  prompt, then → template.
- More than `LLM_MAX_CALLS_PER_RUN` Ollama calls for one `trace_id` → template.

Prompts are versioned files `prompts/<name>_v<N>.md` (the stem is the `prompt_version`; the
highest N wins) with `$variable` placeholders. Raw prompts and variables are never logged, only
`prompt_version` + `input_hash`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import string
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError
from pydantic_core import to_jsonable_python

from tracelayer_agents.audit import sha256_json
from tracelayer_agents.config import DEFAULT_LLM_TIMEOUT_S, llm_timeout_s
from tracelayer_agents.llm.templates import TEMPLATES
from tracelayer_agents.schemas import LLMMeta, LLMProvider

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

PROMPTS_DIR = Path(__file__).parent / "prompts"
_PROMPT_STEM_RE = re.compile(r"^(?P<name>[a-z0-9_]+?)_v(?P<version>\d+)$")

HEALTH_TIMEOUT_S = 2.0
TEMPLATE_MODEL = "template"
SYSTEM_PROMPT = "Reply with a single JSON object that matches the given schema. No other text."


def _discover_prompts() -> dict[str, Path]:
    """prompt name → newest versioned file."""
    found: dict[str, tuple[int, Path]] = {}
    for path in PROMPTS_DIR.glob("*.md"):
        m = _PROMPT_STEM_RE.match(path.stem)
        if not m:
            raise RuntimeError(f"prompt file {path.name!r} is not named <name>_v<N>.md")
        version = int(m["version"])
        if m["name"] not in found or version > found[m["name"]][0]:
            found[m["name"]] = (version, path)
    return {name: path for name, (_, path) in found.items()}


PROMPTS = _discover_prompts()

_missing_templates = sorted(PROMPTS.keys() - TEMPLATES.keys())
if _missing_templates:
    raise RuntimeError(f"prompts without a deterministic template: {_missing_templates}")


@dataclass(frozen=True)
class LLMSettings:
    provider: LLMProvider = "ollama"
    ollama_host: str = "http://localhost:11434"
    ollama_model: str | None = None  # None → first model listed by GET /api/tags
    timeout_s: float = DEFAULT_LLM_TIMEOUT_S  # whole Ollama phase of one call
    max_calls_per_run: int = 6

    @classmethod
    def from_env(cls) -> LLMSettings:
        provider = os.environ.get("LLM_PROVIDER", "ollama").strip().lower() or "ollama"
        if provider not in ("ollama", "template"):
            raise ValueError(f"LLM_PROVIDER must be 'ollama' or 'template', got {provider!r}")
        return cls(
            provider=provider,  # type: ignore[arg-type]
            ollama_host=os.environ.get("OLLAMA_HOST", "").strip() or cls.ollama_host,
            ollama_model=os.environ.get("OLLAMA_MODEL", "").strip() or None,
            timeout_s=llm_timeout_s(),
            max_calls_per_run=int(
                os.environ.get("LLM_MAX_CALLS_PER_RUN", "") or cls.max_calls_per_run
            ),
        )


@dataclass
class _Attempt:
    trace_id: str
    attempts: int = 0
    model: str | None = None
    errors: list[str] = field(default_factory=list)


class LLMClient:
    """Holds the per-process caches: Ollama health/model list, models that reject `think`, and the
    per-`trace_id` call counts. Use one instance per process (`get_client()`)."""

    def __init__(
        self,
        settings: LLMSettings | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings or LLMSettings.from_env()
        self._transport = transport
        self._health_checked = False
        self._models: list[str] | None = None  # None = unreachable
        self._no_think: set[str] = set()
        self._calls: defaultdict[str, int] = defaultdict(int)

    async def generate_structured(
        self,
        prompt_name: str,
        variables: dict[str, Any],
        output_model: type[T],
        *,
        trace_id: str,
    ) -> tuple[T, LLMMeta]:
        if prompt_name not in PROMPTS:
            raise ValueError(f"unknown prompt {prompt_name!r}")
        prompt_version = PROMPTS[prompt_name].stem
        input_hash = sha256_json({"prompt_version": prompt_version, "variables": variables})
        started = time.perf_counter()
        run = _Attempt(trace_id=trace_id)

        output: T | None = None
        if self.settings.provider == "ollama":
            try:
                output = await asyncio.wait_for(
                    self._try_ollama(prompt_name, variables, output_model, run),
                    timeout=self.settings.timeout_s,
                )
            except TimeoutError:
                run.errors.append("ollama_timeout")

        provider: LLMProvider = "ollama" if output is not None else "template"
        if output is None:
            output = _run_template(prompt_name, variables, output_model)

        meta = LLMMeta(
            provider=provider,
            model=run.model if provider == "ollama" and run.model else TEMPLATE_MODEL,
            prompt_version=prompt_version,
            input_hash=input_hash,
            attempts=run.attempts,
            fallback_used=provider == "template",
            latency_ms=round((time.perf_counter() - started) * 1000, 3),
            errors=run.errors,
        )
        logger.info(
            "llm %s provider=%s model=%s attempts=%d input_hash=%s errors=%s",
            prompt_version,
            meta.provider,
            meta.model,
            meta.attempts,
            input_hash,
            meta.errors,
        )
        return output, meta

    # ----------------------------------------------------------------------------- ollama

    async def _try_ollama(
        self, prompt_name: str, variables: dict[str, Any], output_model: type[T], run: _Attempt
    ) -> T | None:
        try:
            prompt = _render(PROMPTS[prompt_name], variables)
        except (KeyError, ValueError):
            run.errors.append("prompt_render_failed")
            return None

        async with httpx.AsyncClient(
            base_url=self.settings.ollama_host,
            transport=self._transport,
            timeout=self.settings.timeout_s,
        ) as http:
            models = await self._list_models(http)
            if models is None:
                run.errors.append("ollama_unreachable")
                return None
            model = self.settings.ollama_model or (models[0] if models else None)
            if model is None:
                run.errors.append("ollama_no_models")
                return None
            run.model = model

            schema = output_model.model_json_schema()
            for attempt in (1, 2):
                content = await self._chat(http, model, prompt, schema, run)
                if content is None:
                    return None
                try:
                    return output_model.model_validate_json(content)
                except ValidationError as exc:
                    run.errors.append(f"invalid_output_attempt_{attempt}")
                    feedback = json.dumps(
                        exc.errors(include_input=False, include_url=False), default=str
                    )
                    prompt = (
                        f"{prompt}\n\nYour previous reply failed validation with these errors:\n"
                        f"{feedback}\nReply again with only a JSON object matching the schema."
                    )
        return None

    async def _list_models(self, http: httpx.AsyncClient) -> list[str] | None:
        """GET /api/tags once per process; None when Ollama is unreachable."""
        if self._health_checked:
            return self._models
        try:
            resp = await http.get("/api/tags", timeout=HEALTH_TIMEOUT_S)
            resp.raise_for_status()
            self._models = [m.get("name") or m.get("model") for m in resp.json().get("models", [])]
            self._models = [m for m in self._models if m]
        except (httpx.HTTPError, ValueError, AttributeError):
            self._models = None
            self._health_checked = True
            return None
        # Set only once the check finished: a check cancelled by the call deadline is retried.
        self._health_checked = True
        if not self.settings.ollama_model and self._models:
            logger.info("OLLAMA_MODEL unset; using first local Ollama model %r", self._models[0])
        return self._models

    async def _chat(
        self,
        http: httpx.AsyncClient,
        model: str,
        prompt: str,
        schema: dict[str, Any],
        run: _Attempt,
    ) -> str | None:
        """One /api/chat call (plus one retry without `think` if the model rejects it). Returns
        the message content, or None after recording why in `run.errors`."""
        while True:
            if self._calls[run.trace_id] >= self.settings.max_calls_per_run:
                run.errors.append("llm_call_cap_reached")
                return None
            self._calls[run.trace_id] += 1
            run.attempts += 1

            body: dict[str, Any] = {
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
                "format": schema,
                "options": {"temperature": 0.1, "num_predict": 1200},
            }
            send_think = model not in self._no_think
            if send_think:
                body["think"] = False  # keep reasoning text out of the JSON reply

            try:
                resp = await http.post("/api/chat", json=body)
            except httpx.TimeoutException:
                run.errors.append("ollama_timeout")
                return None
            except httpx.TransportError:
                self._models = None  # host went away: fail fast for the rest of the process
                run.errors.append("ollama_unreachable")
                return None

            if resp.status_code == 400 and send_think and "think" in resp.text.lower():
                self._no_think.add(model)
                run.errors.append("ollama_think_unsupported")
                continue
            if resp.status_code == 404:
                run.errors.append("ollama_model_not_found")
                return None
            if resp.status_code >= 400:
                run.errors.append(f"ollama_http_{resp.status_code}")
                return None
            try:
                return str(resp.json()["message"]["content"])
            except (ValueError, KeyError, TypeError):
                return ""  # malformed envelope: fails validation and takes the retry path


def _render(path: Path, variables: dict[str, Any]) -> str:
    rendered = {
        k: v if isinstance(v, str) else json.dumps(to_jsonable_python(v), indent=2)
        for k, v in variables.items()
    }
    return string.Template(path.read_text(encoding="utf-8")).substitute(rendered)


def _run_template(prompt_name: str, variables: dict[str, Any], output_model: type[T]) -> T:
    result = TEMPLATES[prompt_name](variables)
    if isinstance(result, output_model):
        return result
    return output_model.model_validate(result.model_dump())


_default_client: LLMClient | None = None


def get_client() -> LLMClient:
    global _default_client
    if _default_client is None:
        _default_client = LLMClient()
    return _default_client


async def generate_structured(
    prompt_name: str,
    variables: dict[str, Any],
    output_model: type[T],
    *,
    trace_id: str,
) -> tuple[T, LLMMeta]:
    return await get_client().generate_structured(
        prompt_name, variables, output_model, trace_id=trace_id
    )

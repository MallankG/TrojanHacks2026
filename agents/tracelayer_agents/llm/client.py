"""Structured LLM calls with graceful degradation (CLAUDE.md rule 8).

Chain: Ollama (local) or Groq (hosted, `LLM_PROVIDER=groq`) → deterministic template. The demo
works with no network and Ollama not running:
- `LLM_PROVIDER=template` skips the LLM entirely.
- Groq: OpenAI-compatible `/chat/completions` with a `json_schema` response format (best-effort
  mode, so optional fields are allowed); no `GROQ_API_KEY`, rate limit (429), HTTP error or
  timeout → template.
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
from tracelayer_agents.schemas import LLM_SOURCES, LLMMeta, LLMProvider

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


def prompt_input_hash(prompt_name: str, variables: dict[str, Any]) -> str:
    """`LLMMeta.input_hash`: sha256 of the canonical JSON of prompt_version + variables."""
    return sha256_json({"prompt_version": PROMPTS[prompt_name].stem, "variables": variables})

_missing_templates = sorted(PROMPTS.keys() - TEMPLATES.keys())
if _missing_templates:
    raise RuntimeError(f"prompts without a deterministic template: {_missing_templates}")


GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_DEFAULT_MODEL = "openai/gpt-oss-20b"  # free tier, supports json_schema output


@dataclass(frozen=True)
class LLMSettings:
    provider: LLMProvider = "ollama"
    ollama_host: str = "http://localhost:11434"
    ollama_model: str | None = None  # None → first model listed by GET /api/tags
    groq_api_key: str | None = field(default=None, repr=False)
    groq_model: str = GROQ_DEFAULT_MODEL
    groq_base_url: str = GROQ_BASE_URL
    # Groq is an external destination: case evidence is sent only when the deployment lists
    # "groq" in APPROVED_DATA_DESTINATIONS (the backend's egress allowlist); otherwise → template.
    groq_approved: bool = False
    timeout_s: float = DEFAULT_LLM_TIMEOUT_S  # whole Ollama phase of one call
    max_calls_per_run: int = 6

    @classmethod
    def from_env(cls) -> LLMSettings:
        provider = os.environ.get("LLM_PROVIDER", "ollama").strip().lower() or "ollama"
        if provider not in ("ollama", "groq", "template"):
            raise ValueError(
                f"LLM_PROVIDER must be 'ollama', 'groq' or 'template', got {provider!r}")
        return cls(
            provider=provider,  # type: ignore[arg-type]
            ollama_host=os.environ.get("OLLAMA_HOST", "").strip() or cls.ollama_host,
            ollama_model=os.environ.get("OLLAMA_MODEL", "").strip() or None,
            groq_api_key=os.environ.get("GROQ_API_KEY", "").strip() or None,
            groq_model=os.environ.get("GROQ_MODEL", "").strip() or cls.groq_model,
            groq_approved="groq" in _destinations(
                os.environ.get("APPROVED_DATA_DESTINATIONS", "")),
            timeout_s=llm_timeout_s(),
            max_calls_per_run=int(
                os.environ.get("LLM_MAX_CALLS_PER_RUN", "") or cls.max_calls_per_run
            ),
        )

    @property
    def unavailable_reason(self) -> str | None:
        """Error code when the configured provider cannot be called at all (the template answers);
        None when calls are attempted. Reachability, keys' validity and rate limits are per call."""
        if self.provider == "template":
            return "template_only"
        if self.provider == "groq" and not self.groq_api_key:
            return "groq_no_api_key"
        if self.provider == "groq" and not self.groq_approved:
            return "groq_not_approved_destination"
        return None

    @property
    def model(self) -> str | None:
        """The configured model of the active provider (None: Ollama's first listed model)."""
        return self.groq_model if self.provider == "groq" else self.ollama_model


def _destinations(raw: str) -> set[str]:
    """APPROVED_DATA_DESTINATIONS as the backend writes it: a JSON list, or comma-separated."""
    try:
        values = json.loads(raw)
    except ValueError:
        values = raw.split(",")
    return {str(v).strip().lower() for v in values} if isinstance(values, list) else set()


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
        input_hash = prompt_input_hash(prompt_name, variables)
        started = time.perf_counter()
        run = _Attempt(trace_id=trace_id)

        output: T | None = None
        remote = self.settings.provider
        if remote in LLM_SOURCES:
            attempt = self._try_ollama if remote == "ollama" else self._try_groq
            try:
                output = await asyncio.wait_for(
                    attempt(prompt_name, variables, output_model, run),
                    timeout=self.settings.timeout_s,
                )
            except TimeoutError:
                run.errors.append(f"{remote}_timeout")

        provider: LLMProvider = remote if output is not None else "template"
        if output is None:
            output = _run_template(prompt_name, variables, output_model)

        meta = LLMMeta(
            provider=provider,
            model=run.model if provider != "template" and run.model else TEMPLATE_MODEL,
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
            return await self._validated(
                prompt, output_model, run, lambda p: self._chat(http, model, p, schema, run))

    async def _validated(
        self, prompt: str, output_model: type[T], run: _Attempt, send: Any
    ) -> T | None:
        """`send(prompt)` up to twice: a reply that fails `output_model` validation is retried once
        with the errors appended. Model output is parsed with `source=<provider>` so the schemas
        apply their model-only checks."""
        for attempt in (1, 2):
            content = await send(prompt)
            if content is None:
                return None
            try:
                return output_model.model_validate_json(
                    content, context={"source": self.settings.provider})
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

    def _take_call(self, run: _Attempt) -> bool:
        """Count one LLM call against the per-trace cap; False (and recorded) once it is reached."""
        if self._calls[run.trace_id] >= self.settings.max_calls_per_run:
            run.errors.append("llm_call_cap_reached")
            return False
        self._calls[run.trace_id] += 1
        run.attempts += 1
        return True

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
            if not self._take_call(run):
                return None

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

    # ----------------------------------------------------------------------------- groq

    async def _try_groq(
        self, prompt_name: str, variables: dict[str, Any], output_model: type[T], run: _Attempt
    ) -> T | None:
        if reason := self.settings.unavailable_reason:  # no key, or not an approved destination
            run.errors.append(reason)
            return None
        try:
            prompt = _render(PROMPTS[prompt_name], variables)
        except (KeyError, ValueError):
            run.errors.append("prompt_render_failed")
            return None
        run.model = self.settings.groq_model
        schema = output_model.model_json_schema()
        async with httpx.AsyncClient(
            base_url=self.settings.groq_base_url,
            transport=self._transport,
            timeout=self.settings.timeout_s,
            headers={"Authorization": f"Bearer {self.settings.groq_api_key}"},
        ) as http:
            return await self._validated(
                prompt, output_model, run,
                lambda p: self._groq_chat(http, p, output_model.__name__, schema, run))

    async def _groq_chat(
        self, http: httpx.AsyncClient, prompt: str, name: str, schema: dict[str, Any],
        run: _Attempt,
    ) -> str | None:
        """One /chat/completions call. Returns the message content ("" when the reply did not
        match the schema, so the validation retry runs), or None after recording why."""
        if not self._take_call(run):
            return None
        body: dict[str, Any] = {
            "model": self.settings.groq_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
            "max_completion_tokens": 2048,
            # best-effort (non-strict): strict mode needs every field required, our schemas aren't
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": name, "schema": schema}},
        }
        if self.settings.groq_model.startswith("openai/gpt-oss"):
            body["reasoning_effort"] = "low"  # keep reasoning short: it shares the token budget
        try:
            resp = await http.post("/chat/completions", json=body)
        except httpx.TimeoutException:
            run.errors.append("groq_timeout")
            return None
        except httpx.TransportError:
            run.errors.append("groq_unreachable")
            return None
        if resp.status_code == 400 and "json_validate_failed" in resp.text:
            run.errors.append("groq_schema_mismatch")
            try:  # the rejected reply, so the retry gets the real validation errors
                return str(resp.json()["error"].get("failed_generation") or "")
            except (ValueError, KeyError, TypeError, AttributeError):
                return ""
        if resp.status_code == 413:
            run.errors.append("groq_request_too_large")  # over the free tier's tokens-per-minute
            return None
        if resp.status_code == 429:
            run.errors.append("groq_rate_limited")
            return None
        if resp.status_code >= 400:
            run.errors.append(f"groq_http_{resp.status_code}")
            return None
        try:
            return str(resp.json()["choices"][0]["message"]["content"])
        except (ValueError, KeyError, IndexError, TypeError):
            return ""


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

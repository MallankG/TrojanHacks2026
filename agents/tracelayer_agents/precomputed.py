"""The precomputed golden run: a demo safety net for when the local model is slow.

`python -m tracelayer_agents.demo --golden --save` runs the golden sequence live and writes every
result to `mocks/fixtures/golden_run.json`. With `AgentConfig.use_precomputed`
(env `TRACELAYER_PRECOMPUTED=1`), the two LLM nodes return the stored output instead of calling the
LLM, but only for the exact golden request:

- `draft_narrative`: same alert_id (content-addressed: sha256 of the graph snapshot's edges and the
  transaction id, so it pins the dataset), and every transaction id and chunk id the stored draft
  cites is in the live evidence bundle and retrieved sources;
- `copilot_answer`: same question (whitespace and case ignored), same retrieved chunk_ids;
- `chat_classify` / `chat_draft_answer`: a chat on the golden alert's case, one of
  `GOLDEN_CHAT_CHIPS` or `GOLDEN_CHAT_QUESTION` (whitespace and case ignored), and the live prompt
  variables (tool results, facts, scope) hash to exactly what was recorded.

Anything else, or a missing/invalid file, runs live. The served output keeps the original
provider, model and prompt_version; the result models carry `precomputed=True`. Every other node
(verifier, save_draft, audit chain, human decision) still runs live on the served output, so a
precomputed case can be decided like any other.
"""

from __future__ import annotations

import html
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from tracelayer_agents.mocks import FIXTURES
from tracelayer_agents.schemas import (
    AnalysisResult,
    CaseDecisionResult,
    CaseDraftResult,
    ChatTurnResult,
    CopilotResult,
    LLMMeta,
    NarrativeDraft,
    PolicyAnswer,
    Sha256Hex,
    UtcDatetime,
)

logger = logging.getLogger(__name__)

GOLDEN_RUN_PATH = FIXTURES / "golden_run.json"
GOLDEN_SCENARIO = "fan_out"
GOLDEN_QUESTION = (
    "What should an analyst document when escalating an alert for supervisor review?"
)
GOLDEN_CHAT_CHIPS = ("explain_alert", "trace_forward", "benign_explanations")
GOLDEN_CHAT_QUESTION = "Where did the money go after it left the hub account?"
ChatPrompt = Literal["chat_plan", "chat_answer"]
M = TypeVar("M", bound=BaseModel)


class GoldenLLMOutput(BaseModel):
    """One recorded chat LLM output, keyed in `GoldenRun.chat_outputs` by `chat_key`."""

    model_config = ConfigDict(extra="forbid")

    prompt: ChatPrompt
    output: dict[str, Any]
    meta: LLMMeta


class GoldenRun(BaseModel):
    """What `golden_run.json` holds. `analyses` is keyed by step label (e.g. "fan_out/strict")."""

    model_config = ConfigDict(extra="forbid")

    format_version: Literal[1] = 1
    created_at: UtcDatetime
    seed: int
    dataset_hashes: dict[str, Sha256Hex]
    config_hash: Sha256Hex
    golden_alert_id: str
    golden_question: str
    copilot_source_ids: list[str]
    analyses: dict[str, AnalysisResult]
    case: CaseDraftResult
    copilot: CopilotResult
    decision: CaseDecisionResult | None = None
    chat_turns: list[ChatTurnResult] = Field(default_factory=list)
    chat_outputs: dict[str, GoldenLLMOutput] = Field(default_factory=dict)


_cache: dict[Path, tuple[float, GoldenRun | None]] = {}


def load(path: Path | None = None) -> GoldenRun | None:
    """The golden run at `path` (default `GOLDEN_RUN_PATH`), or None if missing or invalid.
    Cached per file modification time."""
    path = path or GOLDEN_RUN_PATH
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    cached = _cache.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        run: GoldenRun | None = GoldenRun.model_validate_json(path.read_bytes())
    except (OSError, ValidationError) as exc:
        logger.warning("precomputed golden run %s unusable (%s); running live",
                       path.name, type(exc).__name__)
        run = None
    _cache[path] = (mtime, run)
    return run


def normalize_question(question: str) -> str:
    return " ".join(question.split()).casefold()


def golden_draft(
    alert_id: str | None, evidence_tx_ids: set[str], source_ids: set[str]
) -> tuple[NarrativeDraft, LLMMeta] | None:
    """The stored draft and its LLM metadata, only for the exact golden case request."""
    run = load()
    if run is None or alert_id != run.golden_alert_id:
        return None
    draft, meta = run.case.draft, run.case.llm_meta
    if (
        draft is None
        or meta is None
        or not set(draft.evidence_transaction_ids) <= evidence_tx_ids
        or not set(draft.citations) <= source_ids
    ):
        return None
    return draft.model_copy(deep=True), meta.model_copy(deep=True)


def golden_answer(question: str, source_ids: list[str]) -> PolicyAnswer | None:
    """The stored copilot answer (as `verify_citations` left it, so it re-verifies cleanly), only
    for the golden question with the same retrieved chunks."""
    run = load()
    if (
        run is None
        or normalize_question(question) != normalize_question(run.golden_question)
        or source_ids != run.copilot_source_ids
        or run.copilot.answer is None
    ):
        return None
    return run.copilot.answer.model_copy(deep=True)


def _golden_chat_request(chip: str | None, question: str | None) -> bool:
    if chip is not None:
        return chip in GOLDEN_CHAT_CHIPS
    return normalize_question(question or "") == normalize_question(GOLDEN_CHAT_QUESTION)


def chat_key(prompt: ChatPrompt, variables: dict[str, Any], question: str | None) -> str:
    """The input hash a chat LLM call is stored under. A free-text question matching the golden
    one is replaced by its canonical spelling first, so whitespace and case don't matter."""
    from tracelayer_agents.llm.client import prompt_input_hash

    if question and _golden_chat_request(None, question):
        variables = {**variables, "question": html.escape(GOLDEN_CHAT_QUESTION)}
    return prompt_input_hash(prompt, variables)


_recording: dict[str, GoldenLLMOutput] | None = None


@contextmanager
def record_chat() -> Iterator[dict[str, GoldenLLMOutput]]:
    """While open, every live chat LLM output is collected (for `demo --golden --save`)."""
    global _recording
    _recording = {}
    try:
        yield _recording
    finally:
        _recording = None


def record(prompt: ChatPrompt, variables: dict[str, Any], question: str | None,
           output: BaseModel, meta: LLMMeta) -> None:
    if _recording is not None:
        _recording[chat_key(prompt, variables, question)] = GoldenLLMOutput(
            prompt=prompt, output=output.model_dump(mode="json"), meta=meta)


def golden_chat_output(
    prompt: ChatPrompt, alert_id: str, chip: str | None, question: str | None,
    variables: dict[str, Any], model: type[M],
) -> tuple[M, LLMMeta] | None:
    """The stored chat output and its LLM metadata, only for a golden chip or question on the
    golden case whose live prompt variables match the recorded ones exactly."""
    run = load()
    if run is None or alert_id != run.golden_alert_id or not _golden_chat_request(chip, question):
        return None
    stored = run.chat_outputs.get(chat_key(prompt, variables, question))
    if stored is None or stored.prompt != prompt:
        return None
    try:
        return model.model_validate(stored.output), stored.meta.model_copy(deep=True)
    except ValidationError:
        return None

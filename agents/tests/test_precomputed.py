"""The precomputed golden run: served only for the golden alert/question with the flag on, marked
`precomputed=True` with the original provenance; everything else runs live."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from tracelayer_agents import api, precomputed
from tracelayer_agents.config import AgentConfig
from tracelayer_agents.demo import GOLDEN_SEED, golden_hashes, run_golden
from tracelayer_agents.llm import client as llm
from tracelayer_agents.llm.client import LLMClient, LLMSettings
from tracelayer_agents.schemas import CaseStatus

GOLDEN_SUMMARY = "Precomputed golden summary of the outgoing transfers under review."
GOLDEN_ANSWER = "Precomputed golden answer about escalation documentation."
ON = AgentConfig(use_precomputed=True)
OFF = AgentConfig()


@pytest.fixture(autouse=True)
def template_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm, "_default_client", LLMClient(LLMSettings(provider="template")))


@pytest.fixture
async def golden_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> precomputed.GoldenRun:
    """A golden run recorded with the template, relabeled as Ollama output with marker text so a
    served result is recognizable."""
    run = await run_golden(None, OFF)
    case, copilot = run["case"], run["copilot"]
    ollama = {"provider": "ollama", "model": "qwen3.5:9b", "fallback_used": False}
    case = case.model_copy(update={
        "draft": case.draft.model_copy(update={"summary": GOLDEN_SUMMARY, **ollama}),
        "llm_meta": case.llm_meta.model_copy(update=ollama),
    })
    copilot = copilot.model_copy(update={
        "answer": copilot.answer.model_copy(update={"answer": GOLDEN_ANSWER, **ollama}),
    })
    datasets, config_hash = golden_hashes(OFF)
    golden = precomputed.GoldenRun(
        created_at=datetime.now(UTC), seed=GOLDEN_SEED, dataset_hashes=datasets,
        config_hash=config_hash, golden_alert_id=case.alert_id,
        golden_question=precomputed.GOLDEN_QUESTION,
        copilot_source_ids=[c.chunk_id for c in copilot.sources],
        analyses=run["analyses"], case=case, copilot=copilot,
    )
    path = tmp_path / "golden_run.json"
    path.write_text(golden.model_dump_json())
    monkeypatch.setattr(precomputed, "GOLDEN_RUN_PATH", path)
    return golden


async def _fan_out_alerts(config: AgentConfig) -> list[str]:
    api.configure(config=config)
    result = await api.run_analysis("fan_out")
    return [a.alert_id for a in sorted(result.alerts, key=lambda a: a.rank or 0)]


async def test_case_served_for_golden_alert(golden_file: precomputed.GoldenRun) -> None:
    alerts = await _fan_out_alerts(ON)
    assert alerts[0] == golden_file.golden_alert_id

    result = await api.start_case(alerts[0])
    assert result.status == "ok" and result.precomputed is True
    assert result.draft.summary == GOLDEN_SUMMARY
    assert (result.draft.provider, result.draft.model) == ("ollama", "qwen3.5:9b")
    assert result.llm_meta.provider == "ollama"
    assert result.llm_meta.prompt_version == golden_file.case.llm_meta.prompt_version
    assert result.case_status == CaseStatus.AWAITING_REVIEW and not result.verifier_errors

    # the served case is a real paused case: the human decision still works
    decided = await api.resume_case(result.case_id, {
        "action": "escalate", "actor_role": "analyst", "actor_id": "u1",
        "reason": "Fan-out pattern; escalate for supervisor review.",
    })
    assert decided.status == "ok" and decided.case_status == CaseStatus.ESCALATED
    assert api.verify_audit_chain().ok


async def test_case_live_for_other_alert(golden_file: precomputed.GoldenRun) -> None:
    alerts = await _fan_out_alerts(ON)
    result = await api.start_case(alerts[1])
    assert result.status == "ok" and result.precomputed is False
    assert result.draft.summary != GOLDEN_SUMMARY
    assert result.draft.provider == "template"


async def test_case_live_when_flag_off(golden_file: precomputed.GoldenRun) -> None:
    alerts = await _fan_out_alerts(OFF)
    result = await api.start_case(alerts[0])
    assert result.status == "ok" and result.precomputed is False
    assert result.draft.provider == "template"


async def test_case_live_when_file_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(precomputed, "GOLDEN_RUN_PATH", tmp_path / "missing.json")
    alerts = await _fan_out_alerts(ON)
    result = await api.start_case(alerts[0])
    assert result.status == "ok" and result.precomputed is False


async def test_copilot_served_for_golden_question(golden_file: precomputed.GoldenRun) -> None:
    api.configure(config=ON)
    # whitespace and case don't matter
    result = await api.copilot_query("  " + precomputed.GOLDEN_QUESTION.upper())
    assert result.status == "ok" and result.precomputed is True
    assert result.answer.answer == GOLDEN_ANSWER
    assert (result.answer.provider, result.answer.model) == ("ollama", "qwen3.5:9b")
    assert result.answer.citations == golden_file.copilot.answer.citations


async def test_copilot_live_for_other_question(golden_file: precomputed.GoldenRun) -> None:
    api.configure(config=ON)
    result = await api.copilot_query("When is a SAR due for continuing activity?")
    assert result.status == "ok" and result.precomputed is False
    assert result.answer.answer != GOLDEN_ANSWER and result.answer.provider == "template"


async def test_copilot_live_when_flag_off(golden_file: precomputed.GoldenRun) -> None:
    api.configure(config=OFF)
    result = await api.copilot_query(precomputed.GOLDEN_QUESTION)
    assert result.status == "ok" and result.precomputed is False
    assert result.answer.provider == "template"


def test_flag_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRACELAYER_PRECOMPUTED", raising=False)
    assert AgentConfig.from_env().use_precomputed is False
    monkeypatch.setenv("TRACELAYER_PRECOMPUTED", "1")
    assert AgentConfig.from_env().use_precomputed is True


async def test_shipped_golden_run_matches_fixtures() -> None:
    """The committed golden_run.json loads and still points at the deterministic fan_out top
    alert, so TRACELAYER_PRECOMPUTED=1 actually serves it."""
    golden = precomputed.load()
    assert golden is not None
    assert golden.dataset_hashes == golden_hashes(OFF)[0]
    run = await run_golden(None, OFF)
    assert run["case"].alert_id == golden.golden_alert_id
    assert golden.case.draft is not None and not golden.case.draft.fallback_used
    assert golden.copilot.answer is not None and not golden.copilot.answer.fallback_used

"""Runtime settings for the agent workflows. The backend passes an `AgentConfig` to the facade;
`AgentConfig.from_env()` is the default."""

from __future__ import annotations

import os

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Ollama budget for one structured LLM call (all attempts). Local models are slow on a laptop.
DEFAULT_LLM_TIMEOUT_S = 120.0
# Headroom an LLM node keeps beyond the LLM budget, so the client can still fall back to the
# template before the node itself times out.
LLM_NODE_HEADROOM_S = 30.0


def llm_timeout_s() -> float:
    """`LLM_TIMEOUT_S` from the env (the one place it is read; `llm.client` uses this too)."""
    return float(os.environ.get("LLM_TIMEOUT_S", "").strip() or DEFAULT_LLM_TIMEOUT_S)


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # Per-node wall-clock limit. Sync service calls run in a worker thread that can't be killed,
    # so a timeout abandons the thread and fails the node.
    node_timeout_s: float = Field(default=30.0, gt=0)
    # Wall-clock limit for nodes that call the LLM (draft_narrative, copilot_answer). Must exceed
    # LLM_TIMEOUT_S so a slow model ends in the template fallback, never a node timeout.
    llm_node_timeout_s: float = Field(
        default_factory=lambda: llm_timeout_s() + LLM_NODE_HEADROOM_S, gt=0
    )

    # Alert tiers come from the composite rank: top 5% high, next to 20% medium, rest low.
    tier_high_top_pct: float = Field(default=0.05, gt=0, le=1)
    tier_medium_top_pct: float = Field(default=0.20, gt=0, le=1)

    # investigate_flows: the top N alerts by rank, plus every typology-rule hit, capped.
    investigate_top_n: int = Field(default=3, ge=1)
    max_investigations: int = Field(default=10, ge=1)

    # retrieve_policy: chunks requested from PolicyRetriever.search.
    policy_top_k: int = Field(default=5, ge=1)

    source_asset_id: str = "amlsim_transactions_v1"

    @model_validator(mode="after")
    def _tiers_ordered(self) -> AgentConfig:
        if self.tier_high_top_pct > self.tier_medium_top_pct:
            raise ValueError("tier_high_top_pct must not exceed tier_medium_top_pct")
        return self

    @model_validator(mode="after")
    def _llm_node_outlasts_llm(self) -> AgentConfig:
        budget = llm_timeout_s()
        if self.llm_node_timeout_s <= budget:
            raise ValueError(
                f"llm_node_timeout_s ({self.llm_node_timeout_s}) must be greater than "
                f"LLM_TIMEOUT_S ({budget}), or the node times out before the template fallback"
            )
        return self

    @classmethod
    def from_env(cls) -> AgentConfig:
        overrides: dict[str, float] = {}
        if timeout := os.environ.get("TRACELAYER_NODE_TIMEOUT_S", "").strip():
            overrides["node_timeout_s"] = float(timeout)
        if llm_timeout := os.environ.get("TRACELAYER_LLM_NODE_TIMEOUT_S", "").strip():
            overrides["llm_node_timeout_s"] = float(llm_timeout)
        return cls(**overrides)

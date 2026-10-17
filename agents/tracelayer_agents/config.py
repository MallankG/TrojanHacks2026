"""Runtime settings for the agent workflows. The backend passes an `AgentConfig` to the facade;
`AgentConfig.from_env()` is the default."""

from __future__ import annotations

import os

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # Per-node wall-clock limit. Sync service calls run in a worker thread that can't be killed,
    # so a timeout abandons the thread and fails the node.
    node_timeout_s: float = Field(default=30.0, gt=0)

    # Alert tiers come from the composite rank: top 5% high, next to 20% medium, rest low.
    tier_high_top_pct: float = Field(default=0.05, gt=0, le=1)
    tier_medium_top_pct: float = Field(default=0.20, gt=0, le=1)

    # investigate_flows: the top N alerts by rank, plus every typology-rule hit, capped.
    investigate_top_n: int = Field(default=3, ge=1)
    max_investigations: int = Field(default=10, ge=1)

    source_asset_id: str = "amlsim_transactions_v1"

    @model_validator(mode="after")
    def _tiers_ordered(self) -> AgentConfig:
        if self.tier_high_top_pct > self.tier_medium_top_pct:
            raise ValueError("tier_high_top_pct must not exceed tier_medium_top_pct")
        return self

    @classmethod
    def from_env(cls) -> AgentConfig:
        timeout = os.environ.get("TRACELAYER_NODE_TIMEOUT_S")
        return cls(node_timeout_s=float(timeout)) if timeout else cls()

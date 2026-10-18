"""Authenticated read-only HTTP adapter for the canonical GraphBase engine."""
from decimal import Decimal
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import AwareDatetime, Field, model_validator

from app.core.security import ANY_CALLER, CurrentUser
from app.schemas import Body
from app.api.routes.graph import Channel
from app.services import graph_engine

router = APIRouter(prefix="/graph/engine", tags=["graph-engine"])


class EngineAnalysis(Body):
    operation: Literal["neighborhood", "trace", "paths", "cycles", "metrics", "motifs", "snapshot"]
    account_id: UUID
    target_account_id: UUID | None = None
    hops: int = Field(default=3, ge=1, le=6)
    start: AwareDatetime | None = None
    end: AwareDatetime | None = None
    max_nodes: int = Field(default=300, ge=2, le=300)
    direction: Literal["downstream", "upstream"] = "downstream"
    graph_direction: Literal["incoming", "outgoing", "both"] = "both"
    channels: list[Channel] | None = Field(default=None, max_length=9)
    min_amount: Decimal | None = Field(default=None, ge=0, le=10**12)
    max_gap_hours: float = Field(default=72, gt=0, le=744)
    min_amount_ratio: float = Field(default=0, ge=0, le=1)
    min_hops: int = Field(default=3, ge=2, le=6)
    limit: int = Field(default=20, ge=1, le=100)
    motif: Literal["fan_in", "fan_out", "split_merge"] = "fan_out"
    min_branches: int = Field(default=3, ge=2, le=50)
    window_hours: float = Field(default=72, gt=0, le=744)

    @model_validator(mode="after")
    def validate_scope(self):
        if self.start and self.end:
            from datetime import timedelta
            if self.end < self.start or self.end - self.start > timedelta(days=120):
                raise ValueError("start/end must be ordered and within 120 days")
        if self.operation == "cycles" and self.min_hops > self.hops:
            raise ValueError("min_hops must not exceed hops")
        return self


@router.get("/health")
def health(user: CurrentUser = Depends(ANY_CALLER)) -> dict:
    return graph_engine.health()


@router.post("/analysis")
def analyze(body: EngineAnalysis, user: CurrentUser = Depends(ANY_CALLER)) -> dict:
    return graph_engine.analyze(user.tenant_id, body.model_dump(mode="python"))

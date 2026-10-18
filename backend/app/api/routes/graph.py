"""Graph investigation workbench: account search, neighborhoods, time-respecting paths and bounded
pattern search. Every operation is typed and capped server side; no caller-supplied query text runs."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import AwareDatetime, Field, model_validator

from app.core.db import get_one
from app.core.errors import NotFound
from app.core.security import ANY_CALLER, CurrentUser
from app.schemas import Body
from app.services import graph

router = APIRouter(prefix="/graph", tags=["graph"])

Channel = Literal["ach", "wire", "card", "cash", "check", "p2p", "internal_transfer", "crypto", "other"]
Channels = Annotated[list[Channel] | None, Query(description="Rails to include (repeat the parameter); all when omitted")]
MinAmount = Annotated[Decimal | None, Query(ge=0, le=10**12, description="Minimum USD-equivalent amount")]


def _window_or_422(start: datetime | None, end: datetime | None, cap=graph.MAX_WINDOW) -> None:
    try:
        graph.check_window(start, end, cap)
    except graph.GraphLimitError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/accounts")
def search_accounts(
    q: str | None = Query(default=None, max_length=64, description="Fragment of account_ref"),
    limit: int = Query(default=25, ge=1, le=100),
    user: CurrentUser = Depends(ANY_CALLER),
) -> dict:
    """Accounts to start from, most connected first. Without q, also suggests a starting account."""
    return graph.search_accounts(user.tenant_id, q, limit)


@router.get("/accounts/{account_id}/neighborhood")
def account_neighborhood(
    account_id: UUID,
    hops: int = Query(default=1, ge=1, le=3),
    start: datetime | None = None,
    end: datetime | None = None,
    direction: graph.Direction = "both",
    channel: Channels = None,
    min_amount: MinAmount = None,
    max_nodes: int = Query(default=graph.MAX_NODES, ge=2, le=graph.MAX_NODES),
    user: CurrentUser = Depends(ANY_CALLER),
) -> dict:
    _window_or_422(start, end)
    if not get_one("accounts", user.tenant_id, str(account_id), "id"):
        raise NotFound("Account")
    return graph.neighborhood(user.tenant_id, str(account_id), hops, start, end, direction, channel, min_amount, max_nodes)


@router.get("/paths")
def trace_paths(
    source_account_id: UUID,
    target_account_id: UUID | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    max_hops: int = Query(default=4, ge=1, le=6),
    max_gap_hours: float = Query(default=72, gt=0, le=24 * 30),
    min_amount_ratio: float = Query(default=0.0, ge=0, le=1, description="0 disables the amount-continuity check"),
    limit: int = Query(default=25, ge=1, le=100),
    channel: Channels = None,
    min_amount: MinAmount = None,
    user: CurrentUser = Depends(ANY_CALLER),
) -> dict:
    _window_or_422(start, end)
    if not get_one("accounts", user.tenant_id, str(source_account_id), "id"):
        raise NotFound("Source account")
    if target_account_id and not get_one("accounts", user.tenant_id, str(target_account_id), "id"):
        raise NotFound("Target account")
    return graph.paths(
        user.tenant_id, str(source_account_id), str(target_account_id) if target_account_id else None, start, end,
        max_hops, max_gap_hours, min_amount_ratio, limit, channel, min_amount,
    )


class PatternSearch(Body):
    """A typed pattern operation around one account. Over-limit values are rejected with 422."""

    operation: Literal["cycles", "fan_in", "fan_out", "pass_through"]
    account_id: UUID
    start: AwareDatetime | None = None
    end: AwareDatetime | None = None
    channels: list[Channel] | None = Field(default=None, max_length=9)
    min_amount: Decimal | None = Field(default=None, ge=0, le=10**12)
    # cycles
    min_hops: int = Field(default=3, ge=2, le=10)
    max_hops: int = Field(default=6, ge=3, le=10)
    time_respecting: bool = True
    # cycles and pass-through: largest gap between consecutive hops
    max_gap_hours: float = Field(default=72, gt=0, le=24 * 31)
    # fan-in / fan-out
    min_counterparties: int = Field(default=3, ge=2, le=50)
    max_span_hours: float = Field(default=72, gt=0, le=24 * 31)
    # pass-through
    min_pass_through_ratio: float = Field(default=0.8, gt=0, le=1)
    limit: int = Field(default=20, ge=1, le=50)

    @model_validator(mode="after")
    def _bounds(self):
        if self.min_hops > self.max_hops:
            raise ValueError("min_hops must not exceed max_hops")
        try:
            graph.check_window(self.start, self.end, graph.MAX_PATTERN_WINDOW)
        except graph.GraphLimitError as exc:
            raise ValueError(str(exc)) from exc
        return self


@router.post("/patterns/search")
def pattern_search(body: PatternSearch, user: CurrentUser = Depends(ANY_CALLER)) -> dict:
    if not get_one("accounts", user.tenant_id, str(body.account_id), "id"):
        raise NotFound("Account")
    params = body.model_dump()
    params["account_id"] = str(body.account_id)
    try:
        return graph.pattern_search(user.tenant_id, params)
    except graph.GraphLimitError as exc:
        raise HTTPException(422, str(exc)) from exc

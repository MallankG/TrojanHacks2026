from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.db import get_one
from app.core.errors import NotFound
from app.core.security import ANY_CALLER, CurrentUser
from app.services import graph

router = APIRouter(prefix="/graph", tags=["graph"])


@router.get("/accounts/{account_id}/neighborhood")
def account_neighborhood(
    account_id: UUID,
    hops: int = Query(default=1, ge=1, le=3),
    start: datetime | None = None,
    end: datetime | None = None,
    user: CurrentUser = Depends(ANY_CALLER),
) -> dict:
    if not get_one("accounts", user.tenant_id, str(account_id), "id"):
        raise NotFound("Account")
    return graph.neighborhood(user.tenant_id, str(account_id), hops, start, end)


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
    user: CurrentUser = Depends(ANY_CALLER),
) -> dict:
    if not get_one("accounts", user.tenant_id, str(source_account_id), "id"):
        raise NotFound("Source account")
    return graph.paths(
        user.tenant_id, str(source_account_id), str(target_account_id) if target_account_id else None, start, end,
        max_hops, max_gap_hours, min_amount_ratio, limit,
    )

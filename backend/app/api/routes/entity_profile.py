"""Point-in-time account and entity profile (PRD §13 screen 5)."""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.db import get_one, to_json
from app.core.errors import NotFound
from app.core.security import ANY_CALLER, CurrentUser
from app.services import entity_profile

router = APIRouter(tags=["accounts & transactions"])


@router.get("/accounts/{account_id}/profile")
def account_profile(
    account_id: UUID,
    as_of: datetime | None = Query(default=None, description="Information cut-off (UTC). Defaults to now; later transactions are ignored."),
    observed_days: int = Query(default=7, ge=1, le=30, description="Observed window length, ending at as_of"),
    baseline_days: int = Query(default=30, ge=7, le=180, description="Baseline window length, immediately before the observed window"),
    user: CurrentUser = Depends(ANY_CALLER),
) -> dict:
    account = get_one("accounts", user.tenant_id, str(account_id), entity_profile.ACCOUNT_FIELDS)
    if not account:
        raise NotFound("Account")
    cut_off = entity_profile.parse_ts(as_of) if as_of else datetime.now(UTC)
    return to_json(entity_profile.account_profile(user.tenant_id, account, cut_off, observed_days, baseline_days))

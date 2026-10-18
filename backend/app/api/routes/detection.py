"""Detection runs: typology rules + anomaly baseline (+ XGBoost when available) fused into ranked alerts."""

from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import Field

from app.core.db import get_db, get_one
from app.core.errors import NotFound
from app.core.security import ANY_CALLER, CAN_WRITE_SCORES, CurrentUser
from app.schemas import Body
from app.services.detection.run import run_detection

router = APIRouter(prefix="/scoring", tags=["detection"])


class DetectionRunIn(Body):
    window_days: int | None = Field(default=None, ge=1, le=400)
    max_transactions: int | None = Field(default=None, ge=1, le=20_000)


@router.post("/run", status_code=201)
def start_detection(body: DetectionRunIn | None = None, user: CurrentUser = Depends(CAN_WRITE_SCORES)) -> dict:
    """Synchronous for demo-sized data; bounded by the engine's server-side limits."""
    body = body or DetectionRunIn()
    return run_detection(user, body.window_days, body.max_transactions)


@router.get("/runs/{ranker_run_id}")
def detection_run(ranker_run_id: UUID, user: CurrentUser = Depends(ANY_CALLER)) -> dict:
    run = get_one("model_runs", user.tenant_id, str(ranker_run_id))
    if not run or run["model_kind"] != "ranker":
        raise NotFound("Detection run")
    alerts = (get_db().table("alerts").select("id", count="exact", head=True).eq("tenant_id", user.tenant_id)
              .eq("model_run_id", str(ranker_run_id)).execute())
    return {**run, "alert_count": alerts.count}

"""Model runs, features and risk signals. Written by the model-training/scoring worker, read by the UI."""

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import Limit, Offset, ensure_tenant_ids, page
from app.core.config import get_settings
from app.core.db import get_db, get_one, insert_many, to_json, with_actor
from app.core.errors import NotFound
from app.core.security import ANY_CALLER, CAN_WRITE_SCORES, CurrentUser
from app.schemas import FeatureBatch, ModelRunCreate, ModelRunUpdate, SignalBatch

router = APIRouter(tags=["model runs & signals"])


@router.post("/model-runs", status_code=201)
def create_model_run(body: ModelRunCreate, user: CurrentUser = Depends(CAN_WRITE_SCORES)) -> dict:
    row = {**body.model_dump(), "tenant_id": user.tenant_id, "status": "running", "triggered_by": user.id}
    return with_actor(get_db().table("model_runs").insert(to_json(row)), user).execute().data[0]


@router.patch("/model-runs/{run_id}")
def update_model_run(run_id: UUID, body: ModelRunUpdate, user: CurrentUser = Depends(CAN_WRITE_SCORES)) -> dict:
    if not get_one("model_runs", user.tenant_id, str(run_id), "id"):
        raise NotFound("Model run")
    changes = body.model_dump(exclude_unset=True)
    if "artifact_path" in changes:
        path = changes["artifact_path"]
        if path and not path.startswith(f"{user.tenant_id}/"):
            raise HTTPException(422, "artifact_path must start with '<tenant_id>/'")
        changes["artifact_bucket"] = get_settings().model_artifacts_bucket if path else None
    if changes.get("status") in ("completed", "failed"):
        changes["completed_at"] = datetime.now(UTC).isoformat()
    query = get_db().table("model_runs").update(to_json(changes)).eq("tenant_id", user.tenant_id).eq("id", str(run_id))
    return with_actor(query, user).execute().data[0]


@router.get("/model-runs")
def list_model_runs(
    model_kind: Literal["rule_engine", "supervised", "anomaly", "graph_model", "ranker"] | None = None,
    status: Literal["running", "completed", "failed"] | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
    user: CurrentUser = Depends(ANY_CALLER),
) -> dict:
    query = get_db().table("model_runs").select("*", count="exact").eq("tenant_id", user.tenant_id).order("started_at", desc=True)
    if model_kind:
        query = query.eq("model_kind", model_kind)
    if status:
        query = query.eq("status", status)
    return page(query, limit, offset)


@router.get("/model-runs/{run_id}")
def get_model_run(run_id: UUID, user: CurrentUser = Depends(ANY_CALLER)) -> dict:
    run = get_one("model_runs", user.tenant_id, str(run_id))
    if not run:
        raise NotFound("Model run")
    signals = (
        get_db().table("risk_signals").select("id", count="exact", head=True).eq("tenant_id", user.tenant_id)
        .eq("model_run_id", str(run_id)).execute()
    )
    return {**run, "signal_count": signals.count}


@router.post("/features", status_code=201)
def create_features(body: FeatureBatch, user: CurrentUser = Depends(CAN_WRITE_SCORES)) -> dict:
    rows = [{**f.model_dump(), "tenant_id": user.tenant_id} for f in body.features]
    # Composite FKs reject ids that belong to another tenant.
    inserted = insert_many("features", rows, actor=user)
    return {"inserted": len(inserted), "ids": [r["id"] for r in inserted]}


@router.post("/model-runs/{run_id}/signals", status_code=201)
def create_signals(run_id: UUID, body: SignalBatch, user: CurrentUser = Depends(CAN_WRITE_SCORES)) -> dict:
    run = get_one("model_runs", user.tenant_id, str(run_id), "id, status")
    if not run:
        raise NotFound("Model run")
    ensure_tenant_ids("transactions", user.tenant_id,
                      {t for s in body.signals for t in s.evidence_transaction_ids}, "evidence transaction ids")
    rows = []
    for s in body.signals:
        data = s.model_dump(exclude={"client_ref"})
        rows.append({**data, "tenant_id": user.tenant_id, "model_run_id": str(run_id)})
    inserted = insert_many("risk_signals", rows, actor=user)
    return {
        "inserted": len(inserted),
        "signals": [{"client_ref": s.client_ref, "id": r["id"]} for s, r in zip(body.signals, inserted, strict=True)],
    }


@router.get("/model-runs/{run_id}/signals")
def list_signals(
    run_id: UUID,
    triggered: bool | None = None,
    typology: str | None = None,
    limit: Limit = 100,
    offset: Offset = 0,
    user: CurrentUser = Depends(ANY_CALLER),
) -> dict:
    query = (
        get_db().table("risk_signals").select("*", count="exact").eq("tenant_id", user.tenant_id)
        .eq("model_run_id", str(run_id)).order("created_at")
    )
    if triggered is not None:
        query = query.eq("triggered", triggered)
    if typology:
        query = query.eq("typology", typology)
    return page(query, limit, offset)

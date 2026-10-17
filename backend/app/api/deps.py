from typing import Annotated

from fastapi import HTTPException, Query

from app.core.db import fetch_by_ids

Limit = Annotated[int, Query(ge=1, le=500)]
Offset = Annotated[int, Query(ge=0)]


def page(query, limit: int, offset: int) -> dict:
    """Run a PostgREST query with exact count and return a standard page envelope."""
    result = query.range(offset, offset + limit - 1).execute()
    return {"items": result.data or [], "total": result.count, "limit": limit, "offset": offset}


def ensure_tenant_ids(table: str, tenant_id: str, ids, label: str) -> None:
    """Array columns have no FK; verify every referenced id exists in the caller's tenant."""
    wanted = {str(i) for i in ids if i}
    if not wanted:
        return
    found = {r["id"] for r in fetch_by_ids(table, tenant_id, wanted, "id")}
    missing = wanted - found
    if missing:
        raise HTTPException(422, f"Unknown {label}: {sorted(missing)[:10]}")

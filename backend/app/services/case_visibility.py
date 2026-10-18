"""Case filters for tenant-wide views that also project protected case material."""
from collections.abc import Iterable

from fastapi import HTTPException

from app.core.config import get_settings
from app.core.errors import NotFound
from app.core.security import CurrentUser


def authorized_case_ids(user: CurrentUser, case_ids: Iterable[str]) -> set[str]:
    """Omit inaccessible cases, retaining tenant-wide source/model exploration.

    Unexpected authorization/database failures propagate rather than expose material.
    Keep this independent of path-based authorization: aggregate routes have no
    case UUID in their URL and the backend database client bypasses RLS.
    """
    candidates = set(case_ids)
    settings = get_settings()
    if not (settings.enterprise_enabled or settings.case_authorization_enabled):
        return candidates
    from app.services.enterprise import require_case

    allowed = set()
    for case_id in candidates:
        try:
            require_case(user, case_id)
        except NotFound:
            continue
        except HTTPException as exc:
            if exc.status_code not in (403, 404):
                raise
        else:
            allowed.add(case_id)
    return allowed


def case_authorization_enabled() -> bool:
    settings = get_settings()
    return settings.enterprise_enabled or settings.case_authorization_enabled


def authorized_case_rows(user: CurrentUser, limit: int, status: str | None = None) -> tuple[list[dict], int]:
    """Bounded summaries and counts authorized inside SQL, avoiding hidden case counts."""
    from app.core.db import get_db
    rows = []
    total = 0
    while len(rows) < limit:
        page = get_db().rpc("list_authorized_cases", {
            "p_tenant": user.tenant_id, "p_actor": user.id, "p_status": status,
            "p_assigned": False, "p_limit": min(500, limit - len(rows)), "p_offset": len(rows),
        }).execute().data
        batch = page["items"]
        rows.extend(batch)
        total = page["total"]
        if not batch or len(rows) >= total:
            break
    return rows, total

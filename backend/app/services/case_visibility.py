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

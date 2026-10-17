"""Backend-written audit events (database triggers already cover status changes, narratives and decisions)."""

import logging
from contextvars import ContextVar

from app.core.db import get_db, to_json
from app.core.security import CurrentUser

logger = logging.getLogger(__name__)

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


def record(user: CurrentUser, action: str, object_type: str, object_id: str, metadata: dict | None = None) -> None:
    """Append an audit event. Failures are logged rather than breaking the user's request."""
    meta = dict(metadata or {})
    if user.is_service:
        meta.setdefault("service_name", user.service_name)
    try:
        get_db().table("audit_events").insert(to_json({
            "tenant_id": user.tenant_id,
            "actor_id": user.id,
            "actor_type": user.actor_type,
            "action": action,
            "object_type": object_type,
            "object_id": str(object_id),
            "request_id": request_id_var.get(),
            "metadata": meta,
        })).execute()
    except Exception:  # noqa: BLE001 - audit must not take down the request path
        logger.exception("Failed to write audit event %s for %s/%s", action, object_type, object_id)

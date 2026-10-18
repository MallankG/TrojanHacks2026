"""Request metrics never store URLs, query strings, bearer tokens or bodies."""
from datetime import UTC, datetime
import logging

from app.core.db import get_db

log = logging.getLogger(__name__)


def observe_request(request, status_code: int, duration_ms: float, request_id: str):
    actor = getattr(request.state, "actor", None)
    if not actor or (400 <= status_code < 500 and status_code not in (408, 429)):
        return
    path = request.url.path
    operation = None
    if path.startswith("/graph/"):
        operation = "graph_query"
    elif path == "/policy/search":
        operation = "policy_retrieval"
    elif path.endswith("/verification"):
        operation = "copilot_verification"
    elif path.endswith(("/export-url", "/evidence/v1")):
        operation = "evidence_export"
    if not operation:
        return
    success = status_code < 400 and getattr(request.state, "operation_success", True)
    try:
        get_db().table("service_metrics").insert({"tenant_id": actor.tenant_id, "operation": operation,
            "duration_ms": min(duration_ms, 604800000), "success": success,
            "source_ref": request_id[:160], "observed_at": datetime.now(UTC).isoformat()}).execute()
    except Exception:
        log.warning("Operation metric persistence unavailable for %s", operation)

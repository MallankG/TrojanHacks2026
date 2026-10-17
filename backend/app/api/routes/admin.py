"""Audit trail, current-user info and tenant user administration."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import Limit, Offset, page
from app.core import audit
from app.core.db import get_db, to_json, with_actor
from app.core.errors import NotFound
from app.core.security import ADMIN_ONLY, ANY_USER, CurrentUser
from app.schemas import UserCreate, UserUpdate

router = APIRouter(tags=["audit & admin"])


@router.get("/me")
def me(user: CurrentUser = Depends(ANY_USER)) -> dict:
    tenant = get_db().table("tenants").select("id, slug, name, is_synthetic").eq("id", user.tenant_id).single().execute().data
    return {"id": user.id, "email": user.email, "display_name": user.display_name, "role": user.role, "tenant": tenant}


@router.get("/audit/events")
def audit_events(
    object_type: str | None = None,
    object_id: str | None = None,
    action: str | None = None,
    actor_id: UUID | None = None,
    since: datetime | None = None,
    limit: Limit = 100,
    offset: Offset = 0,
    user: CurrentUser = Depends(ANY_USER),
) -> dict:
    query = get_db().table("audit_events").select("*", count="exact").eq("tenant_id", user.tenant_id)
    for column, value in (("object_type", object_type), ("object_id", object_id), ("action", action),
                          ("actor_id", str(actor_id) if actor_id else None)):
        if value:
            query = query.eq(column, value)
    if since:
        query = query.gte("occurred_at", since.isoformat())
    return page(query.order("occurred_at", desc=True), limit, offset)


@router.get("/admin/users")
def list_users(user: CurrentUser = Depends(ADMIN_ONLY)) -> list[dict]:
    return (get_db().table("profiles").select("id, display_name, role, created_at").eq("tenant_id", user.tenant_id)
            .order("created_at").execute().data)


@router.post("/admin/users", status_code=201)
def create_user(body: UserCreate, user: CurrentUser = Depends(ADMIN_ONLY)) -> dict:
    """Create a confirmed auth user and provision their profile in the admin's tenant."""
    db = get_db()
    try:
        created = db.auth.admin.create_user({
            "email": body.email, "password": body.password, "email_confirm": True,
            "user_metadata": {"display_name": body.display_name or body.email},
        })
    except Exception as exc:  # noqa: BLE001 - surface auth errors (duplicate email, weak password) to the admin
        raise HTTPException(409, f"Could not create user: {exc}") from exc
    new_id = created.user.id
    # The on_auth_user_created trigger inserted a tenant-less profile; provision it.
    profile = with_actor(db.table("profiles").update(to_json({
        "tenant_id": user.tenant_id, "role": body.role, "display_name": body.display_name or body.email,
    })).eq("id", new_id), user).execute().data[0]
    audit.record(user, "user.provisioned", "profile", new_id, {"role": body.role, "email": body.email})
    return profile


@router.patch("/admin/users/{user_id}")
def update_user(user_id: UUID, body: UserUpdate, user: CurrentUser = Depends(ADMIN_ONLY)) -> dict:
    db = get_db()
    target = db.table("profiles").select("id, role").eq("id", str(user_id)).eq("tenant_id", user.tenant_id).execute().data
    if not target:
        raise NotFound("User")
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(422, "Nothing to update")
    if str(user_id) == user.id and changes.get("role", "admin") != "admin":
        raise HTTPException(409, "Admins cannot remove their own admin role")
    updated = db.table("profiles").update(changes).eq("id", str(user_id)).eq("tenant_id", user.tenant_id).execute().data[0]
    audit.record(user, "user.updated", "profile", str(user_id), {"from_role": target[0]["role"], **changes})
    return updated

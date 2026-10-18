"""Authentication and role-based authorization.

Two kinds of callers:
  * Users (browser) send a Supabase access token. Role and tenant come from public.profiles
    (written only by the backend), never from user-editable auth metadata.
  * Internal workers (model training, agents) send X-Service-Key + X-Tenant-Id and act as role "service".
"""

import hmac
from dataclasses import dataclass
from functools import lru_cache
from uuid import UUID

import jwt
from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_settings
from app.core.db import get_db

bearer = HTTPBearer(auto_error=False)

USER_ROLES = ("admin", "investigator", "analyst", "reviewer", "data_steward")
SERVICE_ROLE = "service"


@dataclass(frozen=True)
class CurrentUser:
    id: str | None  # None for service callers
    tenant_id: str
    role: str
    email: str | None = None
    display_name: str | None = None
    service_name: str | None = None
    aal: str = "aal1"

    @property
    def is_service(self) -> bool:
        return self.role == SERVICE_ROLE

    @property
    def actor_type(self) -> str:
        return "agent" if self.is_service else "user"


@lru_cache
def _jwks_client() -> jwt.PyJWKClient:
    settings = get_settings()
    return jwt.PyJWKClient(f"{settings.supabase_url}/auth/v1/.well-known/jwks.json", cache_keys=True)


def decode_token(token: str) -> dict:
    settings = get_settings()
    try:
        algorithm = jwt.get_unverified_header(token).get("alg")
        if algorithm == "HS256":
            if not settings.supabase_jwt_secret:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "HS256 tokens require SUPABASE_JWT_SECRET")
            key = settings.supabase_jwt_secret
        elif algorithm in ("ES256", "RS256", "EdDSA"):
            key = _jwks_client().get_signing_key_from_jwt(token).key
        else:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unsupported token algorithm")
        return jwt.decode(
            token, key, algorithms=[algorithm], audience=settings.jwt_audience, issuer=f"{settings.supabase_url}/auth/v1"
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Invalid token: {exc}") from exc


def _service_caller(service_key: str, tenant_id: str | None, service_name: str | None) -> CurrentUser:
    expected = get_settings().service_api_key
    if not expected or not hmac.compare_digest(service_key.encode(), expected.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid service key")
    try:
        tenant = str(UUID(tenant_id or ""))
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "X-Tenant-Id header must be a tenant UUID") from exc
    if not get_db().table("tenants").select("id").eq("id", tenant).limit(1).execute().data:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Unknown tenant")
    return CurrentUser(id=None, tenant_id=tenant, role=SERVICE_ROLE, service_name=service_name or "worker")


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    x_service_key: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
    x_service_name: str | None = Header(default=None),
) -> CurrentUser:
    if x_service_key:
        user = _service_caller(x_service_key, x_tenant_id, x_service_name)
        return _apply_case_boundary(request, user)
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    claims = decode_token(credentials.credentials)
    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token has no subject")

    rows = get_db().table("profiles").select("id, tenant_id, role, display_name").eq("id", user_id).limit(1).execute().data
    if not rows or not rows[0].get("tenant_id"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account is not provisioned for a tenant")
    profile = rows[0]
    user = CurrentUser(
        id=user_id, tenant_id=profile["tenant_id"], role=profile["role"], email=claims.get("email"),
        display_name=profile.get("display_name"), aal=claims.get("aal", "aal1"),
    )
    return _apply_case_boundary(request, user)


def require_roles(*roles: str):
    allowed = set(roles)

    def dependency(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.role not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Role '{user.role}' cannot perform this action")
        return user

    return dependency


# Role groups used by the routers.
ANY_CALLER = require_roles(*USER_ROLES, SERVICE_ROLE)
ANY_USER = require_roles(*USER_ROLES)
CAN_INGEST = require_roles("admin", "data_steward", "analyst", "investigator")
CAN_STEWARD = require_roles("admin", "data_steward")
CAN_WRITE_SCORES = require_roles("admin", "analyst", SERVICE_ROLE)  # model runs, features, signals, alerts
CAN_INVESTIGATE = require_roles("admin", "investigator", "analyst", "reviewer")
CAN_INVESTIGATE_OR_SERVICE = require_roles("admin", "investigator", "analyst", "reviewer", SERVICE_ROLE)
CAN_REVIEW = require_roles("admin", "reviewer")
CAN_MANAGE_POLICY = require_roles("admin", SERVICE_ROLE)
ADMIN_ONLY = require_roles("admin")


def _apply_case_boundary(request: Request, user: CurrentUser) -> CurrentUser:
    request.state.actor = user
    if not (get_settings().enterprise_enabled or get_settings().case_authorization_enabled):
        return user
    from app.services.enterprise import require_case, check_egress
    from app.core.db import get_one
    parts = request.url.path.strip("/").split("/")
    case_id = None
    if len(parts) > 1 and parts[0] in ("cases", "agent-runs", "narratives"):
        try:
            resource_id = str(UUID(parts[1]))
        except ValueError:
            return user  # FastAPI returns the UUID validation error.
        if parts[0] == "cases":
            case_id = resource_id
        else:
            table = "agent_runs" if parts[0] == "agent-runs" else "case_narratives"
            row = get_one(table, user.tenant_id, resource_id, "case_id")
            if row:
                case_id = row["case_id"]
    if case_id:
        action = "export" if request.url.path.endswith("/export-url") else "read" if request.method == "GET" else "write"
        _, policy, _ = require_case(user, case_id, action)
        if action == "export":
            check_egress("supabase", policy["region"], policy["classification"], False)
    return user

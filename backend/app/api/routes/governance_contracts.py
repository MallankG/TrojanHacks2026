"""Audited immutable replacements for registered source contracts."""
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import Field

from app.core.db import get_db
from app.core.security import CAN_STEWARD, CurrentUser
from app.services.governance import Product, StrictModel

router = APIRouter(prefix="/governance", tags=["governance"])


class ContractReplacement(StrictModel):
    product: Product
    reason: str = Field(min_length=10, max_length=2000)


@router.post("/products/{product_id}/replacements", status_code=201)
def replace_contract(product_id: UUID, body: ContractReplacement, user: CurrentUser = Depends(CAN_STEWARD)):
    """Create and activate the next revision, retaining all previous evidence and references."""
    return get_db().rpc("replace_governance_product", {
        "p_tenant_id": user.tenant_id, "p_product_id": str(product_id), "p_actor_id": user.id,
        "p_definition": body.product.model_dump(mode="json"), "p_reason": body.reason.strip(),
    }).execute().data

from typing import Any

from fastapi import APIRouter, Body, Depends

from app.core.security import ANY_CALLER, CurrentUser
from app.services import passport

router = APIRouter(prefix="/evidence-passports", tags=["cases"])


@router.post("/verify")
def verify_passport(document: dict[str, Any] = Body(...), user: CurrentUser = Depends(ANY_CALLER)) -> dict:
    """Recompute section and package hashes. Stateless: nothing is stored and no case data is read."""
    return passport.verify(document)

from typing import Any

from fastapi import APIRouter, Depends

from app.guests.access import get_guest_grant
from app.models.case_guest import CaseGuest

router = APIRouter()


@router.get("/cases/{case_id}")
async def guest_case(case_id: int, grant: CaseGuest = Depends(get_guest_grant)) -> Any:
    return {"id": case_id}

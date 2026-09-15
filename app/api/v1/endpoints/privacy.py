"""隐私：监护人同意 / 撤回。"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.core.response import ok
from app.db.base import get_db
from app.db.models import User
from app.schemas import GuardianConsentRequest

router = APIRouter(tags=["privacy"])


@router.post("/privacy/guardian-consent")
async def guardian_consent(
    body: GuardianConsentRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if body.agreed:
        user.guardian_consented = True
    await db.commit()
    return ok(None, "已记录")


@router.post("/privacy/withdraw")
async def withdraw_consent(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user.guardian_consented = False
    await db.commit()
    return ok(None, "已撤回")

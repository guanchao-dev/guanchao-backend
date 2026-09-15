from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.core.response import ok
from app.core.utils import new_id
from app.db.base import get_db
from app.db.models import Feedback, User
from app.schemas import FeedbackRequest

router = APIRouter(tags=["legal"])


@router.get("/legal/latest")
async def legal_latest():
    return ok(
        {
            "privacyPolicyUrl": "https://example.com/legal/privacy",
            "childrenRuleUrl": "https://example.com/legal/children-rule",
            "userAgreementUrl": "https://example.com/legal/user-agreement",
            "version": "2026-08-01",
        }
    )


@router.get("/help/faq")
async def help_faq():
    return ok(
        {
            "list": [
                {"q": "赶海需要看潮汐吗？", "a": "需要。退潮后潮间带才会露出来，涨潮前要及时离开水边。"},
                {"q": "小朋友可以一个人去赶海吗？", "a": "不可以。未成年人一定要有大人陪同，安全第一。"},
                {"q": "观察到的生物可以带回家吗？", "a": "建议只看不碰，观察后放回原处，保护海洋生物。"},
                {"q": "潮汐数据准吗？", "a": "潮汐与天气仅供参考，请以海洋预报和现场管理为准。"},
            ]
        }
    )


@router.post("/help/feedback")
async def help_feedback(
    body: FeedbackRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    db.add(
        Feedback(
            id=new_id("fb"),
            user_id=user.id,
            content=body.content,
            contact=body.contact or "",
        )
    )
    await db.commit()
    return ok(None, "感谢反馈")

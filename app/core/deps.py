from typing import Optional

from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import GuardianConsentError, UnauthorizedError
from app.core.security import decode_access_token
from app.db.base import get_db
from app.db.models import User


async def get_current_user(
    authorization: Optional[str] = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> User:
    """必须登录，否则抛 40101。"""
    if not authorization or not authorization.startswith("Bearer "):
        raise UnauthorizedError("未登录")
    token = authorization[len("Bearer "):].strip()
    payload = decode_access_token(token)
    user = await db.get(User, payload.get("sub"))
    if user is None:
        raise UnauthorizedError("用户不存在")
    return user


async def get_optional_user(
    authorization: Optional[str] = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> Optional[User]:
    """可选登录：带有效 token 返回用户，否则返回 None。"""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization[len("Bearer "):].strip()
    try:
        payload = decode_access_token(token)
    except UnauthorizedError:
        return None
    return await db.get(User, payload.get("sub"))


def ensure_consent(user: User) -> None:
    """未成年人需完成监护人同意后才能写入，否则抛 40301。"""
    if user.need_guardian_consent and not user.guardian_consented:
        raise GuardianConsentError("未完成监护人同意")


async def get_identity(
    x_client_id: Optional[str] = Header(default=None, alias="X-Client-Id"),
    user: Optional[User] = Depends(get_optional_user),
) -> tuple[Optional[User], str]:
    """游客用 X-Client-Id 作为身份，登录用 user_id。

    返回 (user, owner_id)：owner_id 为 "user:{id}" 或 "client:{id}"，用于
    观潮观察 / 点亮地图这类「游客可玩、登录同步」的功能。
    """
    if user is not None:
        return user, f"user:{user.id}"
    return None, f"client:{x_client_id or 'guest'}"

import secrets
from typing import Optional

from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import GuardianConsentError, UnauthorizedError
from app.core.security import decode_access_token
from app.db.base import get_db
from app.db.models import User


async def get_current_user(
    authorization: Optional[str] = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> User:
    """必须登录，否则抛 40101。**被封禁的账号一律拒绝。**"""
    if not authorization or not authorization.startswith("Bearer "):
        raise UnauthorizedError("未登录")
    token = authorization[len("Bearer "):].strip()
    payload = decode_access_token(token)
    user = await db.get(User, payload.get("sub"))
    if user is None:
        raise UnauthorizedError("用户不存在")
    if user.banned:
        raise UnauthorizedError("账号已被封禁，如有疑问请联系管理员")
    return user


async def get_optional_user(
    authorization: Optional[str] = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> Optional[User]:
    """可选登录：带有效 token 返回用户，否则返回 None。

    被封禁的账号按**未登录**处理：走到这里的多是「游客可玩、登录同步」的接口
    （观潮、点亮地图），封禁的人降级成游客即可；需要登录的接口由
    `get_current_user` 明确拒绝。
    """
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization[len("Bearer "):].strip()
    try:
        payload = decode_access_token(token)
    except UnauthorizedError:
        return None
    user = await db.get(User, payload.get("sub"))
    if user is not None and user.banned:
        return None
    return user


def ensure_consent(user: User) -> None:
    """未成年人需完成监护人同意后才能写入，否则抛 40301。"""
    if user.need_guardian_consent and not user.guardian_consented:
        raise GuardianConsentError("未完成监护人同意")


async def get_identity(
    user: Optional[User] = Depends(get_optional_user),
) -> tuple[Optional[User], str]:
    """身份：**必须登录**，未登录抛 40101。

    这个依赖原来允许游客（拿 X-Client-Id 当 `client:{id}`），观潮 / 点亮地图 / 上传图片 /
    上传点位 / 图鉴点亮这些「游客可玩、登录同步」的功能全走它。
    **2026-10-08 起统一收紧成必须登录** —— 游客写入这条路没了，登录时合并游客数据
    （`_merge_guest_data`）也就基本用不上了，保留只是为了兼容库里已有的 client 数据。
    """
    if user is None:
        raise UnauthorizedError("未登录")
    return user, f"user:{user.id}"


async def get_guest_identity(
    x_client_id: Optional[str] = Header(default=None, alias="X-Client-Id"),
    user: Optional[User] = Depends(get_optional_user),
) -> tuple[Optional[User], str]:
    """**允许游客**的身份。只有确实不需要账号的场景才用它。

    目前只有研学报到（`POST /records/checkin`）：学生扫老师给的码报到，身份由表单里的
    姓名 + 学号自带，不该强制他们先注册登录。
    """
    if user is not None:
        return user, f"user:{user.id}"
    return None, f"client:{x_client_id or 'guest'}"


async def require_admin(
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
) -> bool:
    """管理接口鉴权：请求头 X-Admin-Token 必须与 settings.admin_token 一致。

    未配置 ADMIN_TOKEN 时一律拒绝 —— 绝不能因为「配置是空、请求头也是空」就放行。
    compare_digest 做常数时间比较，避免按字符命中耗时泄露令牌；两边都 encode 成 bytes，
    因为 compare_digest 传非 ASCII 的 str 会直接抛 TypeError（令牌含中文是可能的）。
    """
    expected = settings.admin_token or ""
    if not expected:
        raise UnauthorizedError("管理接口未启用（未配置 ADMIN_TOKEN）")
    provided = x_admin_token or ""
    if not provided or not secrets.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
        raise UnauthorizedError("管理令牌无效")
    return True

"""微信登录 code2session。未配置 AppID/Secret 时进入 mock 模式，方便本地联调。"""
import httpx

from app.core.config import settings
from app.core.exceptions import BadRequestError


async def code2session(code: str) -> dict:
    """用 code 换取 openid。返回 {"openid": str, "mock": bool}。"""
    if not settings.wechat_appid or not settings.wechat_secret:
        return {"openid": f"dev_{code}", "mock": True}

    url = "https://api.weixin.qq.com/sns/jscode2session"
    params = {
        "appid": settings.wechat_appid,
        "secret": settings.wechat_secret,
        "js_code": code,
        "grant_type": "authorization_code",
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url, params=params)
    data = resp.json()
    if "openid" not in data:
        raise BadRequestError(f"微信登录失败：{data.get('errmsg', '未知错误')}")
    return {"openid": data["openid"], "mock": False}

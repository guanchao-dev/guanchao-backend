"""通用地理接口：坐标 -> 城市名。

给小程序首页顶部「当前城市」用 —— 之前那里是写死的「青岛」，用户人不在青岛也照样显示。
走服务端反查（而不是小程序直连地图服务）是因为 API 域名已经配好白名单，
不用再去微信后台加 apis.map.qq.com。
"""
from fastapi import APIRouter, Query

from app.core.response import ok
from app.services import amap

router = APIRouter(tags=["geo"])


@router.get("/geo/city")
async def resolve_city(
    lat: float = Query(..., ge=-90, le=90),
    lng: float = Query(..., ge=-180, le=180),
):
    """按坐标反查城市 / 区。查不到（未配置 Key / 高德报错 / 无网络）返回空串。"""
    geo = await amap.regeo(lat, lng)
    return ok({"city": geo.get("city", ""), "district": geo.get("district", "")})

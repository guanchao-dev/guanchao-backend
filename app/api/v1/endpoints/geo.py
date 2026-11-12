"""通用地理接口：坐标 -> 城市名。

给小程序首页顶部「当前城市」用 —— 之前那里是写死的「青岛」，用户人不在青岛也照样显示。
走服务端反查（而不是小程序直连地图服务）是因为 API 域名已经配好白名单，
不用再去微信后台加 apis.map.qq.com。
"""
import time

from fastapi import APIRouter, Query

from app.core.response import ok
from app.services import amap

router = APIRouter(tags=["geo"])

# 内存缓存：坐标 -> (过期时刻, 结果)
# 高德逆地理是外部请求，实测 680ms 左右，而小程序**首页一进去就调这个**（顶部城市），
# 是启动链路上最慢的一个。同一个人反复进出首页、或者同一片区的多个用户，
# 结果都是一样的，没必要每次都去问高德。
_geo_cache: dict[str, tuple[float, dict]] = {}
_GEO_TTL = 1800  # 秒
# 坐标取到小数点后两位再当 key：0.01° ≈ 1km，同一个区不会因为 GPS 抖几下就换城市
_GEO_PRECISION = 2


def _cached_city(lat: float, lng: float) -> dict | None:
    hit = _geo_cache.get(f"{lat:.{_GEO_PRECISION}f},{lng:.{_GEO_PRECISION}f}")
    if hit is not None and hit[0] > time.monotonic():
        return hit[1]
    return None


@router.get("/geo/city")
async def resolve_city(
    lat: float = Query(..., ge=-90, le=90),
    lng: float = Query(..., ge=-180, le=180),
):
    """按坐标反查城市 / 区。查不到（未配置 Key / 高德报错 / 无网络）返回空串。"""
    cached = _cached_city(lat, lng)
    if cached is not None:
        return ok(cached)
    geo = await amap.regeo(lat, lng)
    data = {"city": geo.get("city", ""), "district": geo.get("district", "")}
    # 只缓存查到的结果：查不到（高德挂了/没配 Key）不该把空结果缓存半小时
    if data["city"]:
        _geo_cache[f"{lat:.{_GEO_PRECISION}f},{lng:.{_GEO_PRECISION}f}"] = (
            time.monotonic() + _GEO_TTL,
            data,
        )
    return ok(data)

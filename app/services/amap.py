"""高德地图 Web 服务：地点搜索 + AOI 边界（兴趣面）。

用途：组织者搜「石老人海水浴场」，直接拿到该地点的**真实边界**，
而不是自己画一个圆。拿到边界就用多边形围栏，拿不到就退回圆形围栏。

配置：`.env` 里的 `AMAP_KEY`（高德 Web 服务 Key）。
- 未配置 Key：所有函数返回空，调用方自动退回圆形围栏，不影响签到流程。
- AOI 边界查询属于高德「高阶服务」，需要在开放平台**工单申请开通**；
  未开通时该接口会报错，这里同样返回空，自动降级。

注意：Key 只放在后端，不要下发到小程序。
"""
import httpx

from app.core.config import settings
from app.services.geo import normalize_polygon

_SEARCH_URL = "https://restapi.amap.com/v3/place/text"
# AOI 边界查询（高阶服务，开通后使用）。endpoint 可配，便于按高德实际下发的地址调整。
_AOI_URL = "https://restapi.amap.com/v3/place/aoi"


def enabled() -> bool:
    return bool(settings.amap_key)


async def search_poi(keyword: str, city: str = "", limit: int = 10) -> list[dict]:
    """关键字搜索地点，返回候选列表（含 poiId / 名称 / 地址 / 坐标）。"""
    if not enabled() or not keyword.strip():
        return []
    params = {
        "key": settings.amap_key,
        "keywords": keyword.strip(),
        "offset": min(max(limit, 1), 25),
        "page": 1,
        "extensions": "all",
    }
    if city:
        params["city"] = city
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(_SEARCH_URL, params=params)
        data = resp.json()
    except Exception:
        return []
    if str(data.get("status")) != "1":
        return []
    out = []
    for p in (data.get("pois") or [])[:limit]:
        loc = str(p.get("location") or "").split(",")
        if len(loc) != 2:
            continue
        try:
            lng, lat = float(loc[0]), float(loc[1])
        except ValueError:
            continue
        out.append(
            {
                "poiId": p.get("id") or "",
                "name": p.get("name") or "",
                "address": p.get("address") or "",
                "district": p.get("adname") or "",
                "city": p.get("cityname") or "",
                "lat": lat,
                "lng": lng,
                # 高德对部分面状 POI 会直接带上边界坐标串
                "polygon": normalize_polygon(p.get("polyline") or ""),
            }
        )
    return out


async def get_aoi_polygon(poi_id: str) -> list[list[float]]:
    """按 POI id 取 AOI 边界（[[lng, lat], ...]）。未开通权限 / 失败时返回空。"""
    if not enabled() or not poi_id:
        return []
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(
                _AOI_URL, params={"key": settings.amap_key, "id": poi_id, "output": "json"}
            )
        data = resp.json()
    except Exception:
        return []
    if str(data.get("status")) != "1":
        return []
    # 不同版本字段名可能是 shape / polyline / aoi
    for key in ("shape", "polyline", "aoi"):
        pts = normalize_polygon(data.get(key))
        if pts:
            return pts
    aois = data.get("aois") or []
    if aois and isinstance(aois, list):
        for key in ("shape", "polyline"):
            pts = normalize_polygon(aois[0].get(key))
            if pts:
                return pts
    return []


async def resolve_fence(keyword: str, city: str) -> dict | None:
    """搜地点并尽量取到真实边界，返回首个带边界的候选。"""
    for poi in await search_poi(keyword, city, limit=10):
        if poi.get("polygon"):
            return poi
        pts = await get_aoi_polygon(poi.get("poiId") or "")
        if pts:
            poi["polygon"] = pts
            return poi
    return None

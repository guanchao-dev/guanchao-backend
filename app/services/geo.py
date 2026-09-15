"""地理计算：球面距离、点在多边形内判定。

围栏支持两种：
- 圆形围栏：圆心 + 半径（radius_m），默认方式；
- 多边形围栏：真实边界（如高德 AOI 边界），polygon 非空时优先使用。
"""
from math import asin, cos, radians, sin, sqrt

_EARTH_R = 6371000.0


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> int:
    """两点球面距离（米）。"""
    p1, p2 = radians(lat1), radians(lat2)
    dp, dl = radians(lat2 - lat1), radians(lng2 - lng1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return int(2 * _EARTH_R * asin(sqrt(a)))


def distance_text(m: int) -> str:
    return f"{m / 1000:.1f} 公里" if m >= 1000 else f"{m} 米"


def normalize_polygon(raw) -> list[list[float]]:
    """把各种来源的边界整理成 [[lng, lat], ...]。

    支持：[[lng, lat], ...]、[{"lng":..,"lat":..}]、高德的 "lng,lat;lng,lat" 字符串。
    点数少于 3 个视为无效，返回空列表。
    """
    pts: list[list[float]] = []
    if isinstance(raw, str):
        for pair in raw.replace("|", ";").split(";"):
            pair = pair.strip()
            if not pair:
                continue
            parts = pair.split(",")
            if len(parts) != 2:
                continue
            try:
                pts.append([float(parts[0]), float(parts[1])])
            except ValueError:
                continue
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                try:
                    pts.append([float(item[0]), float(item[1])])
                except (TypeError, ValueError):
                    continue
            elif isinstance(item, dict):
                lng = item.get("lng", item.get("longitude"))
                lat = item.get("lat", item.get("latitude"))
                if lng is not None and lat is not None:
                    try:
                        pts.append([float(lng), float(lat)])
                    except (TypeError, ValueError):
                        continue
    return pts if len(pts) >= 3 else []


def point_in_polygon(lat: float, lng: float, polygon: list) -> bool:
    """射线法判断点是否在多边形内。polygon 为 [[lng, lat], ...]。"""
    pts = polygon or []
    n = len(pts)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        try:
            xi, yi = float(pts[i][0]), float(pts[i][1])
            xj, yj = float(pts[j][0]), float(pts[j][1])
        except (IndexError, TypeError, ValueError):
            j = i
            continue
        # 边跨越该纬度，且交点在点的左侧
        if (yi > lat) != (yj > lat):
            x_cross = (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi
            if lng < x_cross:
                inside = not inside
        j = i
    return inside


def in_fence(
    lat: float, lng: float, fence_lat: float, fence_lng: float,
    radius_m: int, polygon: list | None = None,
) -> tuple[bool, int, str]:
    """判断是否在围栏内。

    有 polygon 时用多边形判定（返回距圆心的近似距离，仅供展示）；
    否则用「圆心 + 半径」。

    返回 (是否在内, 参考距离米, 说明文案)。
    """
    dist = haversine_m(fence_lat, fence_lng, lat, lng)
    pts = polygon or []
    if len(pts) >= 3:
        if point_in_polygon(lat, lng, pts):
            return True, dist, f"在签到范围内（距中心约 {distance_text(dist)}）"
        return False, dist, f"不在签到范围内（距中心约 {distance_text(dist)}）"
    ok = dist <= radius_m
    if ok:
        return True, dist, f"距签到点约 {distance_text(dist)}（需在 {distance_text(radius_m)} 内）"
    return False, dist, f"距签到点约 {distance_text(dist)}，需要在 {distance_text(radius_m)} 内"

"""场地探索：场地框、网格编号公式、二维码、探索徽章。

网格编号必须与前端同一公式（gridSizeM 由服务端定）：
    latM = 111320
    lngM = 111320 * cos(中点纬度)
    row  = floor( (lat - minLat) * latM / gridSizeM )
    col  = floor( (lng - minLng) * lngM / gridSizeM )
    id   = "{venueId}:{row}:{col}"
"""
import math

# 已知场地的显式框（未列出的场地按点位经纬度派生默认框，避免 5xx）
VENUES: dict[str, dict] = {
    "spot_qd_shilaoren": {
        "name": "青岛 · 石老人",
        "city": "青岛",
        "center": {"latitude": 36.0932, "longitude": 120.4768},
        "bbox": {"minLat": 36.088, "minLng": 120.468, "maxLat": 36.099, "maxLng": 120.488},
        "gridSizeM": 40,
        "scale": 16,
        "needGuardian": True,
    },
}

_DEFAULT_GRID_SIZE_M = 40
_DEFAULT_DLAT = 0.005
_DEFAULT_DLNG = 0.008

# 营地位 / 打卡二维码（无效码一律 40401）
QR_CODES: dict[str, dict] = {
    "GC-CAMP-2026-081": {
        "venueId": "spot_qd_shilaoren",
        "gridIds": [
            "spot_qd_shilaoren:3:7",
            "spot_qd_shilaoren:3:8",
            "spot_qd_shilaoren:4:7",
            "spot_qd_shilaoren:4:8",
        ],
        "exploreRatio": 0.18,
    },
}


def _derive_bbox(lat: float, lng: float) -> dict:
    return {
        "minLat": round(lat - _DEFAULT_DLAT, 6),
        "minLng": round(lng - _DEFAULT_DLNG, 6),
        "maxLat": round(lat + _DEFAULT_DLAT, 6),
        "maxLng": round(lng + _DEFAULT_DLNG, 6),
    }


def venue_config(venue_id: str, spot: object | None) -> dict:
    """返回场地配置。优先显式配置，其次按点位派生，最后回落到石老人默认。"""
    cfg = VENUES.get(venue_id)
    if cfg is not None:
        return dict(cfg)
    if spot is not None and spot.lat is not None and spot.lng is not None:
        return {
            "name": spot.name,
            "city": spot.city,
            "center": {"latitude": spot.lat, "longitude": spot.lng},
            "bbox": _derive_bbox(spot.lat, spot.lng),
            "gridSizeM": _DEFAULT_GRID_SIZE_M,
            "scale": 16,
            "needGuardian": True,
        }
    return dict(VENUES["spot_qd_shilaoren"])


def grid_id(venue_id: str, lat: float, lng: float, bbox: dict, grid_size_m: int) -> str:
    mid = (bbox["minLat"] + bbox["maxLat"]) / 2
    lat_m = 111320.0
    lng_m = 111320.0 * math.cos(math.radians(mid))
    row = math.floor((lat - bbox["minLat"]) * lat_m / grid_size_m)
    col = math.floor((lng - bbox["minLng"]) * lng_m / grid_size_m)
    return f"{venue_id}:{row}:{col}"


def badge_for(ratio: float) -> str:
    if ratio >= 0.8:
        return "海岸探索家"
    if ratio >= 0.5:
        return "潮间带探险者"
    if ratio >= 0.2:
        return "潮间带初探"
    return "探索起步"

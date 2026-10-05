"""和风天气（QWeather）逐小时预报。

接口（项目方给的示例）：
    GET {host}/weather/v1/hourly/{纬度}/{经度}?hours=240&localTime=true&lang=zh
    请求头 X-QW-Api-Key: <API_KEY>

实际返回（2026-10 实测，v1 的新结构，**不是**老的 code/hourly）：
    { "metadata": {...},
      "hours": [ { "forecastTime": "2026-10-05T17:00+08:00",
                   "condition": {"text": "晴", "code": "100"},
                   "temperature": {"value": 19.58, "unit": "°C"},
                   "wind": {"direction": {"compass": "nnw"}, "scale": 5} } ] }
注意：字段是嵌套的，风向给的是英文罗盘缩写（nnw），要自己转中文。

三条约定：
1. 同一坐标 30 分钟内只打一次第三方（内存缓存）—— 首页、出行建议、观潮记录
   都会问天气，不缓存的话一个页面刷新就能把配额打光。
2. **失败一律返回空值，绝不抛错**：天气是锦上添花，挂了不能连累潮汐和出行建议。
   宁可界面上少一行天气，也不要显示一个假的。
3. 逐小时预报只覆盖「现在往后」的一段时间，**过去的日期取不到** ——
   所以观潮记录是在开始时就把天气存下来的（见 watch.py），不靠事后回查。
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

import httpx

from app.core.config import settings

_TTL_SECONDS = 1800  # 30 分钟
_HOURS = 24
_cache: dict[tuple[float, float], tuple[float, list[dict]]] = {}

# 取不到天气时的返回值：结构保持一致，调用方按空串/None 处理
EMPTY: dict = {"text": "", "tempC": None, "windScale": "", "windDir": "", "waveHint": ""}

# 和风给的是英文罗盘缩写，转成中文。用「偏」的写法（东北偏北）更贴近日常说法
_COMPASS_CN = {
    "n": "北", "nne": "东北偏北", "ne": "东北", "ene": "东北偏东",
    "e": "东", "ese": "东南偏东", "se": "东南", "sse": "东南偏南",
    "s": "南", "ssw": "西南偏南", "sw": "西南", "wsw": "西南偏西",
    "w": "西", "wnw": "西北偏西", "nw": "西北", "nnw": "西北偏北",
}


def enabled() -> bool:
    return bool(settings.weather_api_key)


def _as_int(v) -> int | None:
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return None


def _compass_cn(code) -> str:
    return _COMPASS_CN.get(str(code or "").strip().lower(), "")


def _wave_hint(wind_scale) -> str:
    """由风力粗估浪级。

    ⚠️ 和风的逐小时预报里没有浪高，这是「几级风大约对应什么浪」的经验换算，
    不是实测或预报浪高。界面上只作提示，别当精确数据用。
    """
    n = _as_int(wind_scale)
    if n is None:
        return ""
    if n >= 6:
        return "大浪"
    if n >= 4:
        return "中浪"
    return "轻浪"


def _parse_row(row: dict) -> dict:
    """把一条 hours 记录拍平成我们自己的字段。"""
    cond = row.get("condition") or {}
    temp = row.get("temperature") or {}
    wind = row.get("wind") or {}
    direction = wind.get("direction") or {}
    scale = wind.get("scale")
    return {
        "time": str(row.get("forecastTime") or ""),
        "text": str(cond.get("text") or ""),
        "tempC": _as_int(temp.get("value")),
        "windScale": scale,
        "windDir": _compass_cn(direction.get("compass")),
        "waveHint": _wave_hint(scale),
    }


async def _fetch(lat: float, lng: float) -> list[dict]:
    if not enabled():
        return []
    url = f"{settings.weather_api_host.rstrip('/')}/weather/v1/hourly/{lat}/{lng}"
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(
                url,
                params={"hours": _HOURS, "localTime": "true", "lang": "zh"},
                headers={"X-QW-Api-Key": settings.weather_api_key},
            )
        data = resp.json()
    except Exception:
        return []
    rows = data.get("hours")
    if not isinstance(rows, list):
        return []
    return [_parse_row(r) for r in rows if isinstance(r, dict)]


async def hourly(lat: float, lng: float) -> list[dict]:
    """某坐标的逐小时预报（24 小时，已拍平），带内存缓存。取不到返回空列表。"""
    key = (round(float(lat), 2), round(float(lng), 2))
    hit = _cache.get(key)
    now = time.monotonic()
    if hit is not None and now - hit[0] < _TTL_SECONDS:
        return hit[1]

    rows = await _fetch(lat, lng)
    if rows:
        _cache[key] = (now, rows)
    return rows


def _closest(rows: list[dict], when: datetime) -> dict:
    """挑离 when 最近的那个小时。解析不了时间就退回第一个（当前时次）。"""
    best, best_d = rows[0], None
    for row in rows:
        try:
            t = datetime.fromisoformat(str(row.get("time") or ""))
        except ValueError:
            continue
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        d = abs((t - when).total_seconds())
        if best_d is None or d < best_d:
            best, best_d = row, d
    return best


def line(w: dict, with_temp: bool = False) -> str:
    """把天气拼成「多云、东南风 3 级、轻浪」这种短语；缺的项自动跳过。

    取不到天气时这里返回空串，调用方据此整句不显示 —— 不要拼出
    「今天、风  级、。」这种残句。
    """
    parts: list[str] = []
    if w.get("text"):
        parts.append(str(w["text"]))
    if with_temp and w.get("tempC") is not None:
        parts.append(f"{w['tempC']}℃")
    if w.get("windDir") and w.get("windScale"):
        parts.append(f"{w['windDir']}风 {w['windScale']} 级")
    elif w.get("windScale"):
        parts.append(f"{w['windScale']} 级风")
    if w.get("waveHint"):
        parts.append(str(w["waveHint"]))
    return "、".join(parts)


async def current(lat: float, lng: float, when: datetime | None = None) -> dict:
    """某坐标的天气，返回既有 weather 结构（text / tempC / windScale / windDir / waveHint）。

    when 给了就取离它最近的整点（观潮记录用），否则取当前时次。
    取不到返回 EMPTY 的副本 —— 调用方按空值处理，不要 fallback 成假数据。
    """
    if lat is None or lng is None:
        return dict(EMPTY)
    rows = await hourly(lat, lng)
    if not rows:
        return dict(EMPTY)
    row = _closest(rows, when) if when is not None else rows[0]
    return {
        "text": row.get("text") or "",
        "tempC": row.get("tempC"),
        "windScale": row.get("windScale", ""),
        "windDir": row.get("windDir") or "",
        "waveHint": row.get("waveHint") or "",
    }

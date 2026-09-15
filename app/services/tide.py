"""潮汐服务 + 出门建议规则兜底模板。

- 潮汐：按赶海点位用 EOT20 天文潮模型离线计算（见 tide_predict.SITE_HARMONICS），
  通过 spot→site 映射切换，不同点位潮汐曲线与高低潮各自独立，无需第三方 API。
- 天气：仍为 mock（暂未接第三方）。
"""
from calendar import monthrange
from datetime import datetime, timedelta

from sqlalchemy import select

from app.core.utils import SHANGHAI_TZ, new_id, to_shanghai_iso
from app.db.models import TideCache
from app.services.tide_predict import predict as eot20_predict
from app.services.tide_predict import predict_series as eot20_predict_series

TIDE_SOURCE = "EOT20 天文潮模型"

# spot_id -> site_id（交付包 data/multisite_eot20_selected_grids.csv 的四地点）
SPOT_TO_SITE = {
    "spot_qd_yigong": "qingdao_first_beach",
    "spot_qd_shilaoren": "qingdao_shilaoren",
    "spot_qd_luqinghe": "qingdao_liuqinghe",
    "spot_wh_chengshantou": "weihai_chengshantou",
}
_DEFAULT_SITE = "qingdao_first_beach"


def spot_to_site(spot_id: str) -> str:
    return SPOT_TO_SITE.get(spot_id, _DEFAULT_SITE)


def _minutes(t: str) -> int:
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def _fmt(mins: int) -> str:
    mins %= 24 * 60
    return f"{mins // 60:02d}:{mins % 60:02d}"


# 离场判据：水位从低潮回升到这个高度，就该上岸了。
#
# 比原来的「低潮 + 固定 90 分钟」靠谱——大潮天水位涨得快，离场时间会自动提前；
# 小潮天涨得慢，会自动推后。原来那个固定值在大潮天偏激进（同样 90 分钟，
# 水位可能已经涨了一米多）。
_LEAVE_RISE_M = 0.30


def _next_low_after(tide: dict, now: datetime) -> dict | None:
    """今天 now 之后的下一个低潮点；都过去了就返回 None。"""
    now_min = now.hour * 60 + now.minute
    lows = sorted(
        [p for p in (tide.get("points") or []) if p.get("type") == "low" and p.get("time")],
        key=lambda p: _minutes(p["time"]),
    )
    return next((p for p in lows if _minutes(p["time"]) > now_min), None)


def _rise_time_after_low(tide: dict, low_time: str, rise_m: float = _LEAVE_RISE_M) -> str | None:
    """求低潮之后水位回升 rise_m 米的时刻（分钟精度）。

    拿不到站点（例如库里读出来的老缓存没有 site 字段）、或者 4 小时内没涨到
    该水位（小潮天可能真涨不到），就返回 None，由调用方回退到「低潮 + 90 分钟」。
    """
    site = tide.get("site")
    date_str = tide.get("dataDate") or ""
    if not site or not date_str:
        return None
    try:
        base = _bjt_from_hhmm(date_str, low_time)
        span = 4 * 60
        times = [base + timedelta(minutes=m) for m in range(1, span + 1)]
        heights = eot20_predict_series(site, times)
        target = eot20_predict(site, base) + rise_m
    except Exception:
        return None
    for offset, h in enumerate(heights, start=1):
        if h >= target:
            return _fmt(_minutes(low_time) + offset)
    return None


def _bjt_from_hhmm(date_str: str, hhmm: str) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime.strptime(date_str, "%Y-%m-%d").replace(hour=h, minute=m, tzinfo=SHANGHAI_TZ)


def _interp(pts: list[tuple[datetime, float]], now: datetime) -> float:
    """线性插值 now 处的潮高。pts 已按时间升序。"""
    if not pts:
        return 0.0
    if now <= pts[0][0]:
        return pts[0][1]
    if now >= pts[-1][0]:
        return pts[-1][1]
    for i in range(len(pts) - 1):
        t0, h0 = pts[i]
        t1, h1 = pts[i + 1]
        if t0 <= now <= t1:
            span = (t1 - t0).total_seconds()
            frac = 0.0 if span == 0 else (now - t0).total_seconds() / span
            return h0 + frac * (h1 - h0)
    return pts[-1][1]


def _site_hourly(site_id: str, date_str: str) -> list[dict]:
    """某地点某北京日期的逐时潮位（24 个整点）。"""
    base = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=SHANGHAI_TZ)
    return [
        {"time": f"{h:02d}:00", "heightM": round(eot20_predict(site_id, base.replace(hour=h)), 3)}
        for h in range(24)
    ]


def _site_points(site_id: str, date_str: str) -> list[dict]:
    """某地点某北京日期的高低潮点（逐 10 分钟识别 + 三点抛物线精化到分钟）。"""
    base = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=SHANGHAI_TZ)
    pts: list[tuple[int, float]] = []
    for i in range(-20, 24 * 60 + 20, 10):
        pts.append((i, eot20_predict(site_id, base + timedelta(minutes=i))))

    out: list[dict] = []
    for i in range(1, len(pts) - 1):
        m0, h0 = pts[i - 1]
        m1, h1 = pts[i]
        m2, h2 = pts[i + 1]
        if not (0 <= m1 < 24 * 60):  # 只看当天内的网格点，padding 仅用于比较
            continue
        denom = h0 - 2.0 * h1 + h2
        delta = 0.0 if denom == 0 else 10.0 * (h0 - h2) / (2.0 * denom)
        refined = round(m1 + delta) % (24 * 60)
        if h1 < h0 and h1 <= h2:
            out.append({"time": _fmt(refined), "heightM": round(h1, 2), "type": "low"})
        elif h1 > h0 and h1 >= h2:
            out.append({"time": _fmt(refined), "heightM": round(h1, 2), "type": "high"})
    out.sort(key=lambda p: _minutes(p["time"]))
    return out


def _daily_extremes(site_id: str, date_str: str) -> tuple[list[str], list[str]]:
    """返回某地点某北京日期的 (lowTimes, highTimes)，"HH:MM" 升序。"""
    base = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=SHANGHAI_TZ)
    pts: list[tuple[int, float]] = []
    for i in range(-20, 24 * 60 + 20, 10):
        pts.append((i, eot20_predict(site_id, base + timedelta(minutes=i))))

    lows: list[str] = []
    highs: list[str] = []
    for i in range(1, len(pts) - 1):
        m0, h0 = pts[i - 1]
        m1, h1 = pts[i]
        m2, h2 = pts[i + 1]
        if not (0 <= m1 < 24 * 60):
            continue
        denom = h0 - 2.0 * h1 + h2
        delta = 0.0 if denom == 0 else 10.0 * (h0 - h2) / (2.0 * denom)
        refined = round(m1 + delta) % (24 * 60)
        if h1 < h0 and h1 <= h2:
            lows.append(_fmt(refined))
        elif h1 > h0 and h1 >= h2:
            highs.append(_fmt(refined))
    return sorted(lows), sorted(highs)


def _current_from_hourly(hourly: list[dict], date_str: str, now: datetime) -> tuple[float, str]:
    """从逐时潮位推算当前潮高与趋势。"""
    pts = [(_bjt_from_hhmm(date_str, p["time"]), p["heightM"]) for p in hourly]
    if not pts:
        return 0.0, "unknown"
    pts.sort(key=lambda x: x[0])
    cur = _interp(pts, now)
    prev = _interp(pts, now - timedelta(hours=1))
    trend = "rising" if cur >= prev else "falling"
    return round(cur, 2), trend


def _cache_date(day: str | None) -> str:
    return day or datetime.now(SHANGHAI_TZ).strftime("%Y-%m-%d")


async def _save_tide_cache(db, spot_id: str, date_str: str, tide: dict) -> None:
    """按点位+日期 upsert 当日潮汐曲线（hourly + points），不存与当前时刻绑定的字段。"""
    payload = {
        "hourly": tide.get("hourly") or [],
        "points": tide.get("points") or [],
    }
    existing = (
        await db.execute(
            select(TideCache).where(TideCache.spot_id == spot_id, TideCache.date == date_str)
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(
            TideCache(
                id=new_id("tc"),
                spot_id=spot_id,
                date=date_str,
                source=tide.get("source", ""),
                data=payload,
            )
        )
    else:
        existing.source = tide.get("source", "")
        existing.data = payload
    await db.commit()


async def _load_tide_cache(db, spot_id: str, date_str: str) -> list[dict] | None:
    """读取某点位某日的缓存逐时曲线，无则返回 None。"""
    row = (
        await db.execute(
            select(TideCache).where(TideCache.spot_id == spot_id, TideCache.date == date_str)
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    hourly = (row.data or {}).get("hourly") or []
    return hourly if hourly else None


async def get_tide(spot_id: str, day: str | None = None, db=None) -> dict:
    """获取指定日期的潮汐（EOT20 天文潮，按点位离线计算，无需第三方）。

    返回统一结构：source / updatedAt / dataDate / currentHeightM / trend / points / hourly。
    """
    site = spot_to_site(spot_id)
    now = datetime.now(SHANGHAI_TZ)
    date_str = day or now.strftime("%Y-%m-%d")
    hourly = _site_hourly(site, date_str)
    points = _site_points(site, date_str)
    ref = now if not day else _bjt_from_hhmm(date_str, "12:00")
    current_height, trend = _current_from_hourly(hourly, date_str, ref)
    tide = {
        "source": TIDE_SOURCE,
        # 站点 id：算「水位回升到某高度是几点」要用（见 _rise_time_after_low）
        "site": site,
        "updatedAt": to_shanghai_iso(now),
        "dataDate": date_str,
        "currentHeightM": current_height,
        "trend": trend,
        "points": points,
        "hourly": hourly,
    }
    if db is not None:
        await _save_tide_cache(db, spot_id, _cache_date(day), tide)
    return tide


async def get_tide_window(spot_id: str, now: datetime, db) -> dict:
    """组装以 now 为中心的 ±12 小时滚动潮汐窗口（EOT20 按点位离线计算，自洽连续）。

    - hourly：跨午夜、按时间升序的 24h 显示曲线；
    - points：今天的高低潮点（供赶海建议/AI 使用，避免跨午夜 HH:MM 歧义）。
    """
    now = now.astimezone(SHANGHAI_TZ)
    site = spot_to_site(spot_id)
    today_str = now.strftime("%Y-%m-%d")

    start = (now - timedelta(hours=12)).replace(minute=0, second=0, microsecond=0)
    pts: list[tuple[datetime, float]] = []
    t = start
    while t <= now + timedelta(hours=12):
        pts.append((t, eot20_predict(site, t)))
        t += timedelta(hours=1)

    hourly = [{"time": dt.strftime("%H:%M"), "heightM": round(h, 3)} for dt, h in pts]
    current_height = _interp(pts, now)
    prev_height = _interp(pts, now - timedelta(hours=1))
    trend = "rising" if current_height >= prev_height else "falling"

    return {
        "source": TIDE_SOURCE,
        "updatedAt": to_shanghai_iso(now),
        "dataDate": today_str,
        "currentHeightM": round(current_height, 2),
        "trend": trend,
        "points": _site_points(site, today_str),
        "hourly": hourly,
        "windowHours": 24,
    }


def get_weather(spot_id: str, day) -> dict:
    return {"text": "多云", "tempC": 24, "windScale": 3, "windDir": "东南", "waveHint": "轻浪"}


def _next_change(points: list, now: datetime) -> dict:
    """根据高低潮点计算下一个转折点：低潮后涨、高潮后退。"""
    t = now.hour * 60 + now.minute
    for p in sorted(points, key=lambda x: _minutes(x["time"])):
        m = _minutes(p["time"])
        if m > t:
            if p["type"] == "low":
                return {"time": p["time"], "type": "rising", "label": "开始涨潮"}
            if p["type"] == "high":
                return {"time": p["time"], "type": "falling", "label": "开始退潮"}
            return {"time": p["time"], "type": p["type"], "label": "开始涨潮"}
    first = sorted(points, key=lambda x: _minutes(x["time"]))[0]
    if first["type"] == "low":
        return {"time": first["time"], "type": "rising", "label": "开始涨潮"}
    return {"time": first["time"], "type": "falling", "label": "开始退潮"}


def build_beachcombing_hint(tide: dict, now: datetime) -> dict:
    """基于潮汐和当前时间计算赶海建议：现在适不适合 + 什么时候赶海更好。"""
    points = sorted(
        [p for p in (tide.get("points") or []) if p.get("time")],
        key=lambda p: _minutes(p["time"]),
    )
    now_min = now.hour * 60 + now.minute
    now_date = now.strftime("%Y-%m-%d")
    data_date = tide.get("dataDate", "") or now_date
    trend = tide.get("trend", "unknown")
    height = tide.get("currentHeightM", 0)

    def _unsuitable(reason: str, go_advice: str) -> dict:
        return {
            "suitableNow": False,
            "label": "现在不适合赶海",
            "reason": reason,
            "goAdvice": go_advice,
            "blocked": True,
            "bestWindow": {},
            "hint": "低潮前后潮间带露出来，最适合观察礁石缝里的小生物。",
            "safetyTip": "晚上海边视线差、风险高，请勿夜间赶海；未成年人须有大人陪同。",
        }

    # 1. 潮汐数据过期（是昨天的数据）
    if data_date < now_date:
        return _unsuitable("潮汐数据已过期，暂不适合赶海。", "请稍后刷新获取最新潮汐，等白天退潮时再来。")

    # 2. 凌晨（天亮前）不适合赶海
    if now_min < 360:
        return _unsuitable("现在是凌晨，天还没亮，不适合赶海。", "等天亮了、退潮时再来赶海。")

    # 下一个低潮点（赶海最佳时机在低潮前后）
    best_window = {}
    lows = [p for p in points if p["type"] == "low"]
    if lows:
        next_low = None
        for p in lows:
            if _minutes(p["time"]) > now_min:
                next_low = p
                break
        if next_low is None:
            next_low = lows[0]  # 当天低潮都过了，取次日第一个低潮
        low_t = _minutes(next_low["time"])
        # 右界 = 水位较低潮回升 30cm 的时刻。拿不到站点（老缓存）时退回原来的 low+90。
        best_window = {
            "lowTideTime": next_low["time"],
            "start": _fmt(low_t - 90),
            "end": _rise_time_after_low(tide, next_low["time"]) or _fmt(low_t + 90),
        }

    suitable_now = trend == "falling"
    win = best_window
    if suitable_now:
        label = "适合赶海"
        reason = f"正在退潮，礁石逐渐露出，当前潮高约 {height} 米。"
        if win:
            # 说清具体几点离场：只说「涨潮前离开」太含糊，AI 会自行发挥成高潮时刻（往往
            # 比安全时间晚好几个小时），和界面上的离场时间对不上。
            go_advice = (
                f"现在就可以去，最佳赶海时段是 {win['start']}-{win['end']}"
                f"（低潮 {win['lowTideTime']} 前后），请在 {win['end']} 前离开水边。"
            )
        else:
            go_advice = "现在正在退潮，可以出发赶海，水位回升前记得离开水边。"
    else:
        label = "现在不太适合"
        reason = f"正在涨潮，水位升高，当前潮高约 {height} 米。"
        if win:
            go_advice = (
                f"建议等退潮再去：今天 {win['start']}-{win['end']}"
                f"（低潮 {win['lowTideTime']} 前后）比较适合赶海。"
            )
        else:
            go_advice = "现在正在涨潮，先别下水边，等退潮后再去赶海。"

    return {
        "suitableNow": suitable_now,
        "label": label,
        "reason": reason,
        "goAdvice": go_advice,
        "blocked": False,
        "bestWindow": best_window,
        "hint": "低潮前后潮间带露出来，最适合观察礁石缝里的小生物。",
        "safetyTip": "退潮赶海、涨潮回家；涨潮前请及时离开水边，未成年人须有大人陪同。",
    }


def build_fallback_advice(tide: dict, weather: dict, now: datetime) -> dict:
    trend = tide["trend"]
    height = tide["currentHeightM"]
    rising = trend == "rising"
    nxt = _next_change(tide["points"], now)

    if rising:
        headline = "现在正在涨潮，先别下到礁石区"
        body = (
            f"当前潮高约 {height} 米，正在涨潮，请留在岸边安全的地方观察。"
            f"{nxt['label']}时间约 {nxt['time']}，请提前离开水边。"
        )
        suitable_now = False
    else:
        headline = "现在正在退潮，适合看礁石"
        body = (
            f"当前潮高约 {height} 米，正在退潮，石头缝里的小生物更容易看见。"
            f"{nxt['label']}时间约 {nxt['time']}，请留意水位变化。"
        )
        suitable_now = True

    # 离场时间 = 水位较低潮回升 30cm 的时刻，与 build_beachcombing_hint 的「最佳时段」右界
    # 用同一个函数算，保证界面上的数字和正文一致。
    #
    # 原实现是「若下一个转折点是开始涨潮，就取该时间减 20 分钟」——而「开始涨潮」指的就是
    # 低潮那一刻，所以算出来是「低潮前 20 分钟」。低潮恰恰是水位最低、最适合赶海的时刻，
    # 让用户在那之前离场正好是反的，还会和 body 里的最佳时段自相矛盾。
    leave_before = None
    if suitable_now:
        upcoming_low = _next_low_after(tide, now)
        if upcoming_low is not None:
            leave_before = _rise_time_after_low(tide, upcoming_low["time"]) or _fmt(
                _minutes(upcoming_low["time"]) + 90
            )

    return {
        "headline": headline,
        "body": body,
        "suitableNow": suitable_now,
        "suitableForLowerGrade": True,
        "leaveBefore": leave_before,
        "nextChange": nxt,
        "weatherLine": f"今天{weather['text']}、{weather['windDir']}风 {weather['windScale']} 级、{weather['waveHint']}，体感较舒适。",
        "safetyLine": "请由大人陪同；不要独自下水；以现场警示和官方预警为准。",
        "disclaimer": "潮汐与天气仅供参考，出海或近水活动请以海洋预报和现场管理为准。",
        "generatedBy": "rule",
        "fallback": True,
    }


def _observe_hint(lows: list[str]) -> str:
    if not lows:
        return ""
    # 优先提示白天(06:00-18:00)的低潮观察时段
    for t in lows:
        hour = int(t[:2])
        if 6 <= hour < 12:
            return "上午退潮较适合观察"
        if 12 <= hour < 18:
            return "下午退潮较适合观察"
    return "退潮时段注意观察"


def tide_calendar(spot_id: str, month: str) -> dict:
    """生成某月每天的潮时（EOT20 天文潮，按点位）。month 形如 2026-08。"""
    site = spot_to_site(spot_id)
    year, mon = int(month[:4]), int(month[5:7])
    days = []
    for d in range(1, monthrange(year, mon)[1] + 1):
        date_str = f"{month}-{d:02d}"
        lows, highs = _daily_extremes(site, date_str)
        days.append(
            {
                "date": date_str,
                "lowTimes": lows,
                "highTimes": highs,
                "observeHint": _observe_hint(lows),
            }
        )
    return {"spotId": spot_id, "month": month, "days": days}

"""赶海点位天文潮预测（Tide Model Driver 3 的 Python 移植，17 个主要分潮）。

数据来源：FES2022b 中国近海版（AVISO，1/30° 网格）。每个点位按自己的经纬度取最近
有效格点的调和常数，见 `app/services/spot_harmonics.py`（由
`tools/build_spot_harmonics.py` 生成，点位增删后重跑该脚本即可）。

结果含义：相对 FES2022b 模型平均面的潮位偏差（米），
**不是**中国海图基准上的绝对潮高，尚未做垂直基准校准。

算法逐行对照 vendor/Tide-Model-Driver 的 MATLAB 源码：
  - tmd_astrol.m   天文平黄经 s/h/p/N（这里只需 p 月球近地点、N 月球升交点）
  - tmd_constit.m  各分潮 omega（角频率）与 ph（相对 t0=1992-01-01 的天文相位）
  - tmd_nodal.m    节点订正 pf（振幅系数）、pu（相位订正，弧度）
  - tmd_harp.m     合成：z = Σ pf*(hRe*cos + hIm*sin)，其中
                      tmp = omega*(t-t0秒) + ph + pu

分潮数量：只预测 17 个主要分潮，不含小分潮推算（TMD 的 InferMinor）。FES2022b
提供 34 个分潮，但本预报器的天文常数与节点订正只覆盖这 17 个；补齐其余 17 个
（多为复合分潮）需另行移植，两者差异通常在 2~3 cm 量级，对赶海相对潮位显示可忽略。

换算约定：FES 给的是（振幅 cm，相位 度），本模块用复系数 (hRe, hIm)：
    hRe = A*cos(g)      hIm = A*sin(g)      （A 取米、g 取弧度）
该约定已用旧 EOT20 值在 4 个地点交叉验证（相位差 1~11°、振幅差 2~7%）。
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from app.services.spot_harmonics import SPOT_COORDS, SPOT_HARMONICS

# t0 = 1992-01-01 00:00 UTC，TMD 复系数的参考历元
_T0 = datetime(1992, 1, 1, tzinfo=timezone.utc)
_RAD = math.pi / 180.0

# (name, omega[rad/s], ph[rad], nodal_key)
# omega/ph 来自 tmd_constit.m（分潮固定天文常数，与地点无关）
_ASTRO: list[tuple[str, float, float, str]] = [
    ("SA",  1.990968403205456e-07, 6.2303435066085, "none"),
    ("SSA", 3.982e-07,            3.487600001,    "none"),
    ("MM",  2.6392e-06,           1.964021610,    "mm"),
    ("MF",  5.3234e-06,           1.756042456,    "mf"),
    ("Q1",  6.495854e-05,         5.877717569,    "q1"),
    ("O1",  6.759774e-05,         1.558553872,    "o1"),
    ("P1",  7.252295e-05,         6.110181633,    "none"),
    ("S1",  7.2722e-05,           0.0,            "none"),
    ("K1",  7.292117e-05,         0.173003674,    "k1"),
    ("J1",  7.556036e-05,         2.137025284,    "j1"),
    ("2N2", 1.352405e-04,         4.086699633,    "n2"),
    ("N2",  1.378797e-04,         6.050721243,    "n2"),
    ("M2",  1.405189e-04,         1.731557546,    "n2"),
    ("T2",  1.452450e-04,         0.052841931,    "none"),
    ("S2",  1.454441e-04,         0.0,            "none"),
    ("K2",  1.458423e-04,         3.487600001,    "k2"),
    ("M4",  2.810377e-04,         3.463115091,    "m4"),
]

# 分潮名（顺序即 coastal_grid.bin 里逐格存放的顺序，两边必须一致）
CONSTITUENTS: list[str] = [row[0] for row in _ASTRO]

# 表里查不到、且没有坐标时最后的兜底点位（青岛·第一海水浴场）
_LAST_RESORT_SPOT = "spot_qd_yigong"

# 运行时登记的调和常数：审核通过的新点位由 tide.py 从库里读出后登记，
# 之后 predict 直接可用（进程内缓存，进程重启后按需重新登记）。
_RUNTIME_HARMONICS: dict[str, dict[str, tuple[float, float]]] = {}


def register_harmonics(spot_id: str, harmonics: dict[str, tuple[float, float]]) -> None:
    """登记某点位的调和常数（从库里读出的）。"""
    _RUNTIME_HARMONICS[spot_id] = harmonics


def is_known(spot_id: str) -> bool:
    """静态表或运行时登记里有没有这个点位。"""
    return spot_id in _RUNTIME_HARMONICS or spot_id in SPOT_HARMONICS


def _harmonics_of(spot_id: str) -> dict[str, tuple[float, float]] | None:
    """取点位常数：运行时登记的优先（库里存的更新），其次预计算的静态表。"""
    return _RUNTIME_HARMONICS.get(spot_id) or SPOT_HARMONICS.get(spot_id)


def harmonics_from_coords(lat: float, lng: float) -> dict[str, tuple[float, float]] | None:
    """按坐标从沿海网格插值出调和常数（审核新点位时用）。超出网格范围返回 None。"""
    from app.services import coastal_grid

    return coastal_grid.harmonics_at(lat, lng, CONSTITUENTS)


def _nearest(lat: float, lng: float) -> str | None:
    """离 (lat, lng) 最近的有调和常数的点位。"""
    best: str | None = None
    best_d = float("inf")
    for sid, (sla, slo) in SPOT_COORDS.items():
        # 经度差按纬度收缩，粗略折算成等效距离的平方
        d = (sla - lat) ** 2 + ((slo - lng) * math.cos(math.radians(lat))) ** 2
        if d < best_d:
            best, best_d = sid, d
    return best


def resolve_spot(spot_id: str, lat: float | None = None, lng: float | None = None) -> str:
    """把任意 spot_id 解析成「调和常数表里确实有数据」的点位 id。

    1) 表里有 -> 直接用；
    2) 否则用坐标（优先调用方传的，其次表里记的）找最近的点位；
    3) 都没有 -> 兜底点位。

    用户投稿过审后新增的点位不在表里，走第 2 步就近近似；想精确就把它补进
    seed.py 的 SPOTS 再重跑一次 tools/build_spot_harmonics.py。
    """
    if spot_id in _RUNTIME_HARMONICS or spot_id in SPOT_HARMONICS:
        return spot_id
    use_lat, use_lng = lat, lng
    if use_lat is None or use_lng is None:
        use_lat, use_lng = SPOT_COORDS.get(spot_id, (None, None))
    if use_lat is not None and use_lng is not None:
        near = _nearest(float(use_lat), float(use_lng))
        if near:
            return near
    return _LAST_RESORT_SPOT


def _nodal_corrections(n_deg: float, p_deg: float) -> dict[str, tuple[float, float]]:
    """节点订正，返回 {nodal_key: (pf, pu)}。N/p 输入角度制，pu 输出弧度。

    逐行对照 tmd_nodal.m，含其 0.188/0.189 的历史笔误（Q1 振幅与相位系数不一致），
    为与 MATLAB 结果一致，这里同样照抄。
    """
    rad = _RAD
    n = n_deg * rad
    sinn = math.sin(n)
    cosn = math.cos(n)
    sin2n = math.sin(2.0 * n)
    cos2n = math.cos(2.0 * n)
    sin3n = math.sin(3.0 * n)

    f_m2 = math.sqrt(
        (1.0 - 0.03731 * cosn + 0.00052 * cos2n) ** 2
        + (0.03731 * sinn - 0.00052 * sin2n) ** 2
    )
    u_m2 = math.atan(
        (-0.03731 * sinn + 0.00052 * sin2n) / (1.0 - 0.03731 * cosn + 0.00052 * cos2n)
    )

    return {
        "none": (1.0, 0.0),
        "mm": (1.0 - 0.130 * cosn, 0.0),
        "mf": (1.043 + 0.414 * cosn, (-23.7 * sinn + 2.7 * sin2n - 0.4 * sin3n) * rad),
        "q1": (
            math.sqrt((1.0 + 0.188 * cosn) ** 2 + (0.188 * sinn) ** 2),
            math.atan(0.189 * sinn / (1.0 + 0.189 * cosn)),
        ),
        "o1": (
            math.sqrt(
                (1.0 + 0.189 * cosn - 0.0058 * cos2n) ** 2
                + (0.189 * sinn - 0.0058 * sin2n) ** 2
            ),
            (10.8 * sinn - 1.3 * sin2n + 0.2 * sin3n) * rad,
        ),
        "k1": (
            math.sqrt(
                (1.0 + 0.1158 * cosn - 0.0029 * cos2n) ** 2
                + (0.1554 * sinn - 0.0029 * sin2n) ** 2
            ),
            math.atan(
                (-0.1554 * sinn + 0.0029 * sin2n)
                / (1.0 + 0.1158 * cosn - 0.0029 * cos2n)
            ),
        ),
        "j1": (
            math.sqrt((1.0 + 0.169 * cosn) ** 2 + (0.227 * sinn) ** 2),
            math.atan(-0.227 * sinn / (1.0 + 0.169 * cosn)),
        ),
        "n2": (f_m2, u_m2),
        "k2": (
            math.sqrt(
                (1.0 + 0.2852 * cosn + 0.0324 * cos2n) ** 2
                + (0.3108 * sinn + 0.0324 * sin2n) ** 2
            ),
            math.atan(
                -(0.3108 * sinn + 0.0324 * sin2n) / (1.0 + 0.2852 * cosn + 0.0324 * cos2n)
            ),
        ),
        "m4": (f_m2 ** 2, 2.0 * u_m2),
    }


def _astrol(days_since_t0: float) -> tuple[float, float]:
    """返回月球近地点 p、升交点 N 的平黄经（角度制）。对照 tmd_astrol.m。"""
    t_mjd = days_since_t0 + 48622.0
    t = t_mjd - 51544.4993
    p = (83.3535 + 0.11140353 * t) % 360.0
    n = (125.0445 - 0.05295377 * t) % 360.0
    return p, n


def predict(spot_id: str, t: datetime) -> float:
    """预测某点位在单个 UTC 时刻的潮位偏差（米）。

    spot_id 一般已由 `resolve_spot` 解析过；传未解析的 id 也行（内部再解析一次）。
    """
    if t.tzinfo is None:
        raise ValueError("predict 需要 tz-aware 的 UTC 时间")
    t_utc = t.astimezone(timezone.utc)
    days = (t_utc - _T0).total_seconds() / 86400.0
    t_sec = (t_utc - _T0).total_seconds()
    p_deg, n_deg = _astrol(days)
    nodal = _nodal_corrections(n_deg, p_deg)
    harmonics = _harmonics_of(spot_id)
    if harmonics is None:
        harmonics = _harmonics_of(resolve_spot(spot_id)) or SPOT_HARMONICS[_LAST_RESORT_SPOT]

    z = 0.0
    for name, omega, ph, key in _ASTRO:
        h_re, h_im = harmonics.get(name, (0.0, 0.0))
        pf, pu = nodal[key]
        arg = omega * t_sec + ph + pu
        z += pf * (h_re * math.cos(arg) + h_im * math.sin(arg))
    return z


def predict_series(spot_id: str, times: list[datetime]) -> list[float]:
    """批量预测某点位，返回与输入等长的潮位列表（米）。"""
    return [predict(spot_id, t) for t in times]

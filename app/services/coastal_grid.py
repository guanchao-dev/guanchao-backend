"""沿海调和常数网格：按任意坐标插值出该点的调和常数。

数据文件 `coastal_grid.bin` 由 `tools/build_spot_harmonics.py` 从 FES2022b 生成，
覆盖山东沿海（默认 34.8~38.2°N / 118.8~123.8°E，1/30° 网格，约 2MB）。

用途：用户投稿的新点位不在 `spot_harmonics.SPOT_HARMONICS` 里，审核通过时按它的
坐标在这里插值出调和常数，存进库（见 admin.py）。这样新点位也是"真算出来的"，
而不是拿几十公里外某个点位凑合。

只用标准库（`struct` + `array`）解析，服务器不需要装 numpy。

文件格式（全小端）：
    8s   magic b'GCGRID01'
    iii  nlat, nlon, ncons
    4d   lat0, lon0, dlat, dlon
    f4[] h_re  nlat*nlon*ncons   —— 逐格逐分潮，无效格点为 NaN
    f4[] h_im  同上
"""
from __future__ import annotations

import math
import struct
from array import array
from pathlib import Path

_BIN = Path(__file__).with_name("coastal_grid.bin")
_MAGIC = b"GCGRID01"
_HEADER = struct.Struct("<8siii4d")

_grid: dict | None = None
_grid_loaded = False

# 四角都是陆地时，向外搜索最近有效格点的半径（格数）
_NEAREST_RADIUS = 8


def _load() -> dict | None:
    """载入网格（只载一次）。文件缺失或损坏返回 None，调用方回退。"""
    global _grid, _grid_loaded
    if _grid_loaded:
        return _grid
    _grid_loaded = True
    if not _BIN.is_file():
        return None
    try:
        # array('f') 用本机字节序；服务器与生成机都是 x86-64 小端，与文件一致
        with _BIN.open("rb") as f:
            magic, nlat, nlon, ncons, lat0, lon0, dlat, dlon = _HEADER.unpack(f.read(_HEADER.size))
            if magic != _MAGIC:
                return None
            n = nlat * nlon * ncons
            hre = array("f")
            hre.fromfile(f, n)
            him = array("f")
            him.fromfile(f, n)
    except Exception:  # 文件损坏/被截断都不该拖垮接口
        return None
    _grid = {
        "nlat": nlat, "nlon": nlon, "ncons": ncons,
        "lat0": lat0, "lon0": lon0, "dlat": dlat, "dlon": dlon,
        "hre": hre, "him": him,
    }
    return _grid


def available() -> bool:
    return _load() is not None


def _corner(g: dict, i: int, j: int, k: int):
    """取格点 (i,j) 第 k 个分潮的 (hRe, hIm)；无效返回 None。"""
    if i < 0 or j < 0 or i >= g["nlat"] or j >= g["nlon"]:
        return None
    base = (i * g["nlon"] + j) * g["ncons"] + k
    re = g["hre"][base]
    if re != re:  # NaN
        return None
    return re, g["him"][base]


def _nearest_valid(g: dict, k: int, i0: int, j0: int):
    """以 (i0,j0) 为中心向外找最近的有效格点（四角都是陆地时的兜底）。"""
    for r in range(1, _NEAREST_RADIUS + 1):
        best = None
        for di in range(-r, r + 1):
            for dj in range(-r, r + 1):
                if max(abs(di), abs(dj)) != r:  # 只看这一圈
                    continue
                v = _corner(g, i0 + di, j0 + dj, k)
                if v is None:
                    continue
                d = di * di + dj * dj
                if best is None or d < best[0]:
                    best = (d, v)
        if best is not None:
            return best[1]
    return None


def harmonics_at(lat: float, lng: float, constituents: list[str]) -> dict[str, tuple[float, float]] | None:
    """按坐标插值出该点的调和常数 {分潮: (hRe, hIm)}；超出网格或数据不足返回 None。

    对复系数做双线性插值（等价于对复振幅插值）。四角里有陆地/缺测就把它剔除、
    权重重新归一；四角全无效才退到最近有效格点。
    """
    g = _load()
    if g is None or not constituents:
        return None

    fi = (lat - g["lat0"]) / g["dlat"]
    fj = (lng - g["lon0"]) / g["dlon"]
    i0, j0 = int(math.floor(fi)), int(math.floor(fj))
    if i0 < 0 or j0 < 0 or i0 >= g["nlat"] - 1 or j0 >= g["nlon"] - 1:
        return None
    tf, sf = fi - i0, fj - j0

    corners = (
        (i0, j0, (1.0 - tf) * (1.0 - sf)),
        (i0, j0 + 1, (1.0 - tf) * sf),
        (i0 + 1, j0, tf * (1.0 - sf)),
        (i0 + 1, j0 + 1, tf * sf),
    )

    out: dict[str, tuple[float, float]] = {}
    for k, name in enumerate(constituents):
        wsum = 0.0
        acc_re = 0.0
        acc_im = 0.0
        for ci, cj, w in corners:
            if w <= 0.0:
                continue
            v = _corner(g, ci, cj, k)
            if v is None:
                continue
            acc_re += w * v[0]
            acc_im += w * v[1]
            wsum += w
        if wsum > 0.0:
            out[name] = (acc_re / wsum, acc_im / wsum)
            continue
        near = _nearest_valid(g, k, i0, j0)
        if near is None:
            return None
        out[name] = near
    return out

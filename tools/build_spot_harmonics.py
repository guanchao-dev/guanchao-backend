"""从 FES2022b 中国近海版网格，为每个赶海点生成调和常数（离线一次性工具）。

背景
----
`app/services/tide_predict.py` 是 TMD 的 Python 移植，但原来只带 4 个地点的调和常数
（EOT20），其余点位一律回退到青岛一浴 —— 相距几十公里算出完全一样的潮位。本脚本按每个
点位的经纬度，从 FES2022b 的 1/30° 网格上取最近的有效格点，生成属于自己的调和常数。

用法（本地运行，需要 netCDF4 + numpy；后端运行时不依赖这两个包）
----------------------------------------------------------------
    python tools/build_spot_harmonics.py --fes-dir "D:\\...\\ocean_tide_extrapolated"

输出
----
    app/services/spot_harmonics.py   —— 自动生成，勿手改。点位增删后重跑本脚本即可。

坐标系与约定
------------
FES2022b 给的是振幅（cm）与相位（degrees，相位滞后）。本预报器（TMD 约定）用的是复系数
(hRe, hIm)，合成式为 z = Σ pf*(hRe*cos + hIm*sin)，故换算为：

    hRe = A * cos(g)      hIm = A * sin(g)      （A 取米，g 取弧度）

该约定已用 4 个老地点的 EOT20 值交叉验证（相位差 1~10°，振幅差 2~6%），见 --check-against-eot20。

注意（已实测踩坑）：FES 目录路径含中文时，netCDF4 直接开全路径会 FileNotFoundError，
必须先 chdir 进目录、再用相对文件名打开。
"""
from __future__ import annotations

import argparse
import ast
import math
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEED_PY = ROOT / "app" / "db" / "seed.py"
PREDICT_PY = ROOT / "app" / "services" / "tide_predict.py"
OUT_PY = ROOT / "app" / "services" / "spot_harmonics.py"

# mask 取值：0=原生海洋 1=近岸外推 2=陆地 3=湖泊。0/1 可取，2/3 跳过。
_WET = (0, 1)

# 自检基准：现有 tide_predict.SITE_HARMONICS 里 4 个地点的 EOT20 复系数（米）。
# 只用于校验换算约定是否正确，不参与生成。
_EOT20_REF: dict[str, dict[str, tuple[float, float]]] = {
    "spot_qd_yigong": {
        "M2": (-0.244977593228917, -1.14628552981445),
        "K1": (-0.153274750843761, -0.20567637506385),
        "S2": (0.127073938733716, -0.351090882274598),
    },
    "spot_qd_shilaoren": {
        "M2": (-0.294759136238002, -1.08733370256685),
        "K1": (-0.153274750843761, -0.200436212641841),
        "S2": (0.103493207834676, -0.347160760458091),
    },
    "spot_qd_luqinghe": {
        "M2": (-0.491265227063337, -0.979910372915669),
        "K1": (-0.163755075687779, -0.18209564416481),
        "S2": (0.0327510151375558, -0.357641085302109),
    },
    "spot_wh_chengshantou": {
        "M2": (-0.14672454781625, 0.30523946108202),
        "K1": (-0.225326984146384, -0.0628819490641071),
        "S2": (-0.112663492073192, 0.0104803248440178),
    },
}


def _literal(name: str, path: Path):
    """从模块源码里取出一个模块级字面量赋值，避免 import（本地没装 sqlalchemy）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == name:
                    return ast.literal_eval(node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            return ast.literal_eval(node.value)
    raise SystemExit(f"[x] 在 {path} 里找不到 `{name}`")


def _amp_phase_to_complex(amp_cm: float, phase_deg: float) -> tuple[float, float]:
    """FES2022b 的（振幅 cm，相位 度）-> TMD 复系数（米）。"""
    a = amp_cm / 100.0
    g = math.radians(phase_deg)
    return a * math.cos(g), a * math.sin(g)


def _write_coastal_grid(constituents, grids, lat, lon, mask, box, out_path: Path) -> None:
    """把 box 范围内的每个有效格点的调和常数写成二进制网格。

    审核通过用户新点位时按坐标插值取常数用。格式（全小端，前 8 字节魔数）：

        b'GCGRID01' | int32 nlat,nlon,ncons | f64 lat0,lon0,dlat,dlon
        f32 h_re[nlat*nlon*ncons]   # 无效格点为 NaN
        f32 h_im[nlat*nlon*ncons]

    用 float32 + NaN 而不是单独的掩膜数组：振幅恒 >= 0，NaN 天然表示「这里没有数据」，
    读的时候 math.isnan 判一下即可，省一个数组也省一次判断。
    """
    import numpy as np
    import struct

    la0, la1, lo0, lo1 = box
    ilat = np.where((lat >= la0) & (lat <= la1))[0]
    ilon = np.where((lon >= lo0) & (lon <= lo1))[0]
    if not len(ilat) or not len(ilon):
        raise SystemExit("[x] --grid-box 与数据范围没有交集")
    i0, i1 = int(ilat[0]), int(ilat[-1]) + 1
    j0, j1 = int(ilon[0]), int(ilon[-1]) + 1
    nlat, nlon, ncons = i1 - i0, j1 - j0, len(constituents)

    usable = np.isin(mask[i0:i1, j0:j1], _WET)
    hre = np.full((nlat, nlon, ncons), np.nan, dtype="<f4")
    him = np.full((nlat, nlon, ncons), np.nan, dtype="<f4")

    for k, c in enumerate(constituents):
        amp = np.asarray(grids[c][0][i0:i1, j0:j1], dtype="float64") / 100.0
        ph = np.radians(np.asarray(grids[c][1][i0:i1, j0:j1], dtype="float64"))
        ok = usable & np.isfinite(amp) & (amp < 1e6)
        hre[:, :, k] = np.where(ok, amp * np.cos(ph), np.nan)
        him[:, :, k] = np.where(ok, amp * np.sin(ph), np.nan)

    header = struct.pack(
        "<8siii4d", b"GCGRID01", nlat, nlon, ncons,
        float(lat[i0]), float(lon[j0]), float(lat[1] - lat[0]), float(lon[1] - lon[0]),
    )
    out_path.write_bytes(header + hre.tobytes() + him.tobytes())
    print(f"[ok] 沿海网格 -> {out_path.name}：{nlat}×{nlon} 格（有效 {int(usable.sum())} 个），"
          f"{out_path.stat().st_size / 1024 / 1024:.2f} MB")


def _nearest_wet(lat, lon, mask, la, lo, max_km: float):
    """在 la/lo 附近找最近的有效格点，返回 (i, j, 距离km, mask值)。找不到返回 None。"""
    import numpy as np

    i_c = int(np.argmin(np.abs(lat - la)))
    j_c = int(np.argmin(np.abs(lon - lo)))
    dlat = float(abs(lat[1] - lat[0]))
    dlon = float(abs(lon[1] - lon[0]))
    r_lat = int(max_km / (111.0 * dlat)) + 2
    r_lon = int(max_km / (111.0 * max(0.2, math.cos(math.radians(la))) * dlon)) + 2

    i0, i1 = max(0, i_c - r_lat), min(len(lat), i_c + r_lat + 1)
    j0, j1 = max(0, j_c - r_lon), min(len(lon), j_c + r_lon + 1)
    sub_mask = mask[i0:i1, j0:j1]
    LA, LO = np.meshgrid(lat[i0:i1], lon[j0:j1], indexing="ij")

    d2 = (LA - la) ** 2 + ((LO - lo) * math.cos(math.radians(la))) ** 2
    d2 = np.where(np.isin(sub_mask, _WET), d2, np.inf)
    k = np.unravel_index(int(np.argmin(d2)), d2.shape)
    if not np.isfinite(d2[k]):
        return None
    i, j = i0 + int(k[0]), j0 + int(k[1])
    return i, j, math.sqrt(float(d2[k])) * 111.0, int(sub_mask[k])


def main() -> int:
    ap = argparse.ArgumentParser(description="按赶海点经纬度生成 FES2022b 调和常数表")
    ap.add_argument("--fes-dir", default=os.environ.get("FES2022_DIR", ""),
                    help="ocean_tide_extrapolated 目录（含 *_fes2022.nc 与 mask）")
    ap.add_argument("--max-km", type=float, default=25.0, help="最近有效格点的搜索半径上限（公里）")
    ap.add_argument("--default-spot", default="spot_qd_yigong",
                    help="没有坐标的点位借用哪个点位的常数")
    ap.add_argument("--check-against-eot20", action="store_true",
                    help="与旧 EOT20 值做交叉校验（相位差 >15° 或振幅差 >10% 则告警）")
    ap.add_argument("--grid-box", default="34.8,38.2,118.8,123.8",
                    help="沿海网格范围 lat0,lat1,lng0,lng1（默认覆盖日照/青岛/烟台/威海）；空串则不生成")
    ap.add_argument("--grid-out", default="",
                    help="网格输出路径（默认 app/services/coastal_grid.bin）")
    args = ap.parse_args()

    if not args.fes_dir:
        print("[x] 请用 --fes-dir 指定 FES2022b 目录（或设 FES2022_DIR 环境变量）")
        return 2
    fes_dir = Path(args.fes_dir)
    if not fes_dir.is_dir():
        print(f"[x] 目录不存在：{fes_dir}")
        return 2

    try:
        import numpy as np  # noqa: F401
        import netCDF4
    except ImportError as e:
        print(f"[x] 缺少依赖：{e}。生成脚本需要 netCDF4 + numpy（后端运行时不依赖）。")
        return 2

    spots = _literal("SPOTS", SEED_PY)
    astro = _literal("_ASTRO", PREDICT_PY)
    constituents = [row[0] for row in astro]  # 17 个主要分潮，与预报器严格一致

    # 路径可能含中文：chdir 后用相对文件名打开
    os.chdir(fes_dir)
    mask_ds = netCDF4.Dataset("mask_fes2022B.nc")
    lat = mask_ds.variables["lat"][:]
    lon = mask_ds.variables["lon"][:]
    mask = mask_ds.variables["mask"][:]

    print(f"网格：lat {len(lat)} × lon {len(lon)}，范围 {lat[0]:.3f}~{lat[-1]:.3f}N / "
          f"{lon[0]:.3f}~{lon[-1]:.3f}E")
    print(f"分潮：{len(constituents)} 个 -> {' '.join(constituents)}\n")

    # 预读各分潮网格（17 × 901×1351 float32 ≈ 83MB）
    grids: dict[str, tuple] = {}
    for c in constituents:
        fn = f"{c.lower()}_fes2022.nc"
        if not Path(fn).is_file():
            print(f"[x] 缺少分潮文件：{fn}")
            return 2
        ds = netCDF4.Dataset(fn)
        grids[c] = (ds.variables["amplitude"][:], ds.variables["phase"][:])

    harmonics: dict[str, dict[str, tuple[float, float]]] = {}
    coords: dict[str, tuple[float, float]] = {}
    fallback_ids: list[str] = []
    warns: list[str] = []

    for sp in spots:
        sid = sp["id"]
        la, lo = sp.get("lat"), sp.get("lng")
        if la is None or lo is None:
            fallback_ids.append(sid)
            continue
        hit = _nearest_wet(lat, lon, mask, float(la), float(lo), args.max_km)
        if hit is None:
            warns.append(f"{sid} 在 {args.max_km}km 内找不到有效格点，改用兜底点位")
            fallback_ids.append(sid)
            continue
        i, j, dist_km, m = hit
        h: dict[str, tuple[float, float]] = {}
        bad = False
        for c in constituents:
            amp = float(grids[c][0][i, j])
            ph = float(grids[c][1][i, j])
            if not math.isfinite(amp) or amp > 1e6:  # float32 填充值 1.8446744e19
                bad = True
                break
            h[c] = _amp_phase_to_complex(amp, ph)
        if bad:
            warns.append(f"{sid} 最近格点缺测，改用兜底点位")
            fallback_ids.append(sid)
            continue
        harmonics[sid] = h
        coords[sid] = (float(la), float(lo))
        print(f"  {sid:<24} 格点距 {dist_km:5.2f}km  mask={m}")

    # 无坐标/缺测的点位：借用兜底点位的常数
    if fallback_ids:
        src = harmonics.get(args.default_spot)
        if src is None:
            print(f"[x] 兜底点位 {args.default_spot} 也没数据，无法继续")
            return 1
        for sid in fallback_ids:
            harmonics[sid] = dict(src)
        print(f"\n  借用 {args.default_spot} 的点位：{' '.join(fallback_ids)}")

    # ---- 自检：与旧 EOT20 值比对 ----
    if args.check_against_eot20:
        print("\n自检（FES2022b vs EOT20，相位差/振幅差）：")
        worst_ph = 0.0
        for sid, ref in _EOT20_REF.items():
            if sid not in harmonics:
                continue
            for c, (hre, him) in ref.items():
                if c not in harmonics[sid]:
                    continue
                a_ref = math.hypot(hre, him)
                g_ref = math.degrees(math.atan2(him, hre)) % 360
                hre2, him2 = harmonics[sid][c]
                a_new = math.hypot(hre2, him2)
                g_new = math.degrees(math.atan2(him2, hre2)) % 360
                dg = abs((g_new - g_ref + 180) % 360 - 180)
                da = abs(a_new - a_ref) / a_ref * 100 if a_ref else 0
                worst_ph = max(worst_ph, dg)
                flag = "  <-- 偏差偏大" if (dg > 15 or da > 10) else ""
                print(f"  {sid:<24}{c:<3} Δ相位 {dg:5.1f}°  Δ振幅 {da:4.1f}%{flag}")
        if worst_ph > 15:
            print(f"[!] 最大相位差 {worst_ph:.1f}° 超过 15°，换算约定可能有问题，请先核对")
            return 1

    # ---- 写出 ----
    def fmt_h(h: dict[str, tuple[float, float]]) -> str:
        lines = []
        for c in constituents:
            hre, him = h[c]
            lines.append(f'        "{c}": ({hre!r}, {him!r}),')
        return "\n".join(lines)

    body = []
    for sp in spots:
        sid = sp["id"]
        if sid in harmonics:
            body.append(f'    "{sid}": {{\n{fmt_h(harmonics[sid])}\n    }},')

    coord_lines = "\n".join(
        f'    "{sid}": ({coords[sid][0]!r}, {coords[sid][1]!r}),' for sid in coords
    )

    OUT_PY.write_text(
        f'''"""赶海点位的调和常数（自动生成，请勿手改）。

由 FES2022b 中国近海版（ocean_tide_extrapolated，1/30° 网格）按每个点位的经纬度
取最近有效格点生成。生成命令：

    python tools/build_spot_harmonics.py --fes-dir "<FES2022b 目录>" --check-against-eot20

生成日期：{date.today().isoformat()}
分潮：{len(constituents)} 个主要分潮（与 tide_predict._ASTRO 一一对应）
约定：hRe = A*cos(g)、hIm = A*sin(g)，A 取米、g 取弧度（FES 的 cm/度已换算）

点位增删后重跑脚本即可；SPOT_COORDS 供 tide_predict 就近兜底用。
"""
from __future__ import annotations

# spot_id -> {{分潮: (hRe, hIm)}}，单位米
SPOT_HARMONICS: dict[str, dict[str, tuple[float, float]]] = {{
{chr(10).join(body)}
}}

# spot_id -> (lat, lng)，用于给表里没有的点位就近兜底
SPOT_COORDS: dict[str, tuple[float, float]] = {{
{coord_lines}
}}
''',
        encoding="utf-8",
    )

    # ---- 沿海网格（审核用户新点位时按坐标插值用）----
    if args.grid_box.strip():
        parts = [float(x) for x in args.grid_box.split(",")]
        if len(parts) != 4:
            print("[x] --grid-box 需要 4 个数：lat0,lat1,lng0,lng1")
            return 2
        grid_out = Path(args.grid_out) if args.grid_out else (ROOT / "app" / "services" / "coastal_grid.bin")
        _write_coastal_grid(constituents, grids, lat, lon, mask,
                            (parts[0], parts[1], parts[2], parts[3]), grid_out)

    print(f"\n[ok] 写入 {OUT_PY.relative_to(ROOT)}：{len(harmonics)}/{len(spots)} 个点位")
    if warns:
        print("提醒：")
        for w in warns:
            print(f"  - {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

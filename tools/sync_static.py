"""把前端 assets/ 下的静态资源同步到服务器的静态目录。

背景
----
小程序的图片走「仓库存一份源图 + 服务器服务一份」：project.config.json 的
packOptions.ignore 把 assets/ 排除出主包，代码里引用的是 CDN 地址
（https://<域名>/api/v1/static/assets/...，见后端 app/api/v1/endpoints/assets.py）。
所以每加一张图都要手动传服务器——容易漏传，也容易出现「仓库一份、服务器另一份」
版本不一致（横幅图就踩过这个坑）。

本脚本负责比对两边并只传有差异的。

用法
----
    python tools/sync_static.py            # 比对并同步（只增/改，不删除）
    python tools/sync_static.py --check    # 只比对不传，有差异时退出码 1
    python tools/sync_static.py --orphans  # 额外列出「服务器有、本地没有」的文件

配置（读 .env，都可省略用默认值）
-------------------------------
    SYNC_SSH_HOST=Administrator@42.193.99.114
    SYNC_STATIC_LOCAL=../前端/assets
    SYNC_STATIC_REMOTE=C:/guanchao/data/uploads/static/assets

注意：不删除服务器上的文件。服务器是历史累积（可能还有旧版本还在引用的图），
所以「服务器有、本地没有」只报告，由人决定要不要清理。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from pathlib import Path

# 以 `_` 开头的文件是内部素材（例如 _medal-snippet.txt 设计片段），不上服务器
SKIP_PREFIX = "_"

# 整目录跳过：tabBar 图标微信强制要求本地文件（app.json 里写的是 assets/tab/xxx.png），
# 不能走网络，所以不要传到服务器。
SKIP_DIRS = {"tab"}

# 刻意让「服务器版本优先」的文件。
# 仓库里放的是设计原图，服务器上放的是量化压缩后的版本（体积小、真机加载快），
# 两边会一直有差异——同步时绝不能用仓库的原图把它们覆盖回去。
KEEP_REMOTE = {
    "home/home-camera.png",
    "home/home-science.png",
}

DEFAULTS = {
    "SYNC_SSH_HOST": "Administrator@42.193.99.114",
    "SYNC_STATIC_LOCAL": "../前端/assets",
    "SYNC_STATIC_REMOTE": "C:/guanchao/data/uploads/static/assets",
}

SSH_OPTS = ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no"]


def load_env() -> dict[str, str]:
    """读仓库根目录的 .env（没有就用默认值），只取 SYNC_ 开头的键。"""
    cfg = dict(DEFAULTS)
    root = Path(__file__).resolve().parent.parent
    env_file = root / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            if k in cfg:
                cfg[k] = v.strip().strip('"').strip("'")
    # 环境变量优先级最高
    for k in cfg:
        if os.environ.get(k):
            cfg[k] = os.environ[k]
    return cfg


def local_files(local_root: Path) -> dict[str, tuple[int, str]]:
    """{相对路径: (字节数, md5)}，路径统一用正斜杠。"""
    out: dict[str, tuple[int, str]] = {}
    for p in sorted(local_root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(local_root).as_posix()
        parts = rel.split("/")
        if any(part.startswith(SKIP_PREFIX) for part in parts):
            continue
        if any(part in SKIP_DIRS for part in parts[:-1]):
            continue
        out[rel] = (p.stat().st_size, hashlib.md5(p.read_bytes()).hexdigest())
    return out


def remote_files(host: str, remote_root: str) -> dict[str, tuple[int, str]]:
    """一次 SSH 拿到服务器上的 {相对路径: (字节数, md5)}。

    用 PowerShell 递归列目录并逐个算 MD5；目录不存在就返回空表。
    """
    ps = (
        f"if (Test-Path '{remote_root}') {{ "
        f"Get-ChildItem -Recurse -File '{remote_root}' | ForEach-Object {{ "
        f"$_.FullName.Substring('{remote_root}'.Length + 1) + '|' + $_.Length + '|' + "
        f"(Get-FileHash -LiteralPath $_.FullName -Algorithm MD5).Hash }} }}"
    )
    proc = subprocess.run(
        ["ssh", *SSH_OPTS, host, f'powershell -NoProfile -Command "{ps}"'],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
    )
    if proc.returncode != 0:
        # 连不上/超时：明确报错，不要静默当成「服务器是空的」——那会导致全量重传
        raise RuntimeError(f"SSH 失败（退出码 {proc.returncode}）：{(proc.stderr or '').strip()[:200]}")

    out: dict[str, tuple[int, str]] = {}
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        parts = line.split("|")
        if len(parts) != 3 or not parts[2]:
            continue
        rel = parts[0].replace("\\", "/")
        try:
            size = int(parts[1])
        except ValueError:
            continue
        out[rel] = (size, parts[2].lower())
    return out


def upload(host: str, local_root: Path, remote_root: str, rels: list[str]) -> int:
    ok = 0
    for rel in rels:
        dst = f"{host}:{remote_root}/{rel}"
        proc = subprocess.run(
            ["scp", *SSH_OPTS, "--", str(local_root / rel), dst],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
        )
        if proc.returncode == 0:
            ok += 1
            print(f"    ↑ {rel}")
        else:
            print(f"    ✗ {rel}  scp 失败：{(proc.stderr or '').strip()[:120]}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description="同步前端 assets 到服务器静态目录")
    ap.add_argument("--check", action="store_true", help="只比对不传，有差异时退出码 1")
    ap.add_argument("--orphans", action="store_true", help="列出服务器有、本地没有的文件")
    args = ap.parse_args()

    cfg = load_env()
    root = Path(__file__).resolve().parent.parent
    local_root = (root / cfg["SYNC_STATIC_LOCAL"]).resolve()
    host, remote_root = cfg["SYNC_SSH_HOST"], cfg["SYNC_STATIC_REMOTE"].rstrip("/")

    if not local_root.is_dir():
        print(f"本地目录不存在：{local_root}")
        print("（用 .env 里的 SYNC_STATIC_LOCAL 指到前端 assets 目录）")
        return 2

    print(f"本地：{local_root}")
    print(f"远端：{host}:{remote_root}")
    print()

    local = local_files(local_root)
    try:
        remote = remote_files(host, remote_root)
    except RuntimeError as e:
        print(f"✗ {e}")
        return 2

    missing = [r for r in local if r not in remote]
    differ = [r for r in local if r in remote and local[r] != remote[r]]
    # 刻意保留服务器版的挑出来，不参与上传
    kept = [r for r in differ if r in KEEP_REMOTE]
    changed = [r for r in differ if r not in KEEP_REMOTE]
    same = [r for r in local if r in remote and local[r] == remote[r]]
    orphans = sorted(set(remote) - set(local))

    print(f"本地 {len(local)} 个 | 服务器 {len(remote)} 个")
    print(f"  一致 {len(same)}   待传 {len(missing) + len(changed)}（新增 {len(missing)}、有改动 {len(changed)}）")
    print(f"  服务器多出 {len(orphans)} 个（不会自动删）")
    if kept:
        print(f"  刻意保留服务器版 {len(kept)} 个（不参与同步）")
    print()

    if kept:
        print("以下文件仓库里是原图、服务器上是压缩版，按约定保留服务器版、不上传：")
        for r in kept:
            print(f"    = {r}  仓库 {local[r][0]} 字节 / 服务器 {remote[r][0]} 字节")
        print()

    if missing:
        print("需要新增：")
        for r in missing:
            print(f"    + {r}  ({local[r][0] / 1024:.1f} KB)")
    if changed:
        print("内容有差异：")
        for r in changed:
            print(f"    ~ {r}  本地 {local[r][0]} 字节 / 服务器 {remote[r][0]} 字节")

    if args.orphans and orphans:
        print("\n服务器有、本地没有（历史遗留，自行判断是否清理）：")
        for r in orphans:
            print(f"    ? {r}  ({remote[r][0] / 1024:.1f} KB)")

    if not missing and not changed:
        if kept:
            print(f"✓ 除上面 {len(kept)} 个刻意保留的之外，两边一致，无需同步")
        else:
            print("✓ 两边一致，无需同步")
        return 0

    if args.check:
        print("\n（--check 模式，未上传）")
        return 1

    print("\n开始上传…")
    ok = upload(host, local_root, remote_root, missing + changed)
    print(f"\n完成：{ok}/{len(missing) + len(changed)} 个文件已上传")
    return 0 if ok == len(missing) + len(changed) else 1


if __name__ == "__main__":
    sys.exit(main())

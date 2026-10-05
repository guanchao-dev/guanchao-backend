"""文件存储：腾讯云 COS（配了就启用），否则回退服务器本地磁盘。

对外只暴露 save_bytes / read_bytes / delete_bytes / public_url，业务层不感知底层。

两点说明：
1. 三个 IO 函数是 **async**。COS 的 Python SDK（qcloud_cos）是同步的，直接调会把
   事件循环堵住 —— 用 asyncio.to_thread 扔到线程里跑。同一份代码对本地磁盘也这么走，
   调用方不用关心当前是哪种后端。
2. 没配齐 COS 的四项配置就自动回退本地磁盘。宁可少走 CDN，也不能因为漏配一个环境变量
   让整个上传功能挂掉。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from app.core.config import settings

_UPLOAD_DIR = Path("data/uploads")


def cos_enabled() -> bool:
    """四项都填齐才算启用。"""
    return bool(
        settings.cos_bucket
        and settings.cos_region
        and settings.cos_secret_id
        and settings.cos_secret_key
    )


def public_url(object_key: str) -> str:
    """给前端用的访问地址。

    配了 COS 就是**绝对 URL**（桶是公有读，小程序 mediaUrl 看到 http 开头会原样透传）；
    回退本地时给相对路径 /media/{key}，由小程序自己拼 BASE_URL。
    """
    if not object_key:
        return ""
    if object_key.startswith("http://") or object_key.startswith("https://"):
        # 已经是完整地址（历史数据 / 外部图片），原样返回
        return object_key
    if cos_enabled():
        return (
            f"https://{settings.cos_bucket}.cos.{settings.cos_region}.myqcloud.com/{object_key}"
        )
    return f"/media/{object_key}"


# ---------------- 本地磁盘 ----------------
def _local_path(object_key: str) -> Path:
    return _UPLOAD_DIR / object_key


def _local_save(object_key: str, data: bytes) -> str:
    path = _local_path(object_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path)


def _local_read(object_key: str) -> bytes:
    path = _local_path(object_key)
    if not path.exists():
        raise FileNotFoundError(object_key)
    return path.read_bytes()


def _local_delete(object_key: str) -> None:
    try:
        _local_path(object_key).unlink()
    except FileNotFoundError:
        pass


# ---------------- 腾讯云 COS ----------------
def _client():
    # 延迟导入：没配 COS 的环境完全不用装这个依赖
    from qcloud_cos import CosConfig, CosS3Client

    return CosS3Client(
        CosConfig(
            Region=settings.cos_region,
            SecretId=settings.cos_secret_id,
            SecretKey=settings.cos_secret_key,
            Scheme="https",
        )
    )


def _cos_save(object_key: str, data: bytes) -> None:
    _client().put_object(Bucket=settings.cos_bucket, Body=data, Key=object_key)


def _cos_read(object_key: str) -> bytes:
    from qcloud_cos.cos_exception import CosServiceError

    try:
        resp = _client().get_object(Bucket=settings.cos_bucket, Key=object_key)
    except CosServiceError as exc:
        # 只把「对象不存在」翻成 FileNotFoundError（调用方按 404 处理）；
        # 其它错误（密钥错、桶名错）原样抛出去，别伪装成 404 让人查不出来。
        if exc.get_status_code() == 404:
            raise FileNotFoundError(object_key) from exc
        raise
    return resp["Body"].get_raw_stream().read()


def _cos_delete(object_key: str) -> None:
    _client().delete_object(Bucket=settings.cos_bucket, Key=object_key)


# ---------------- 对外 ----------------
async def save_bytes(object_key: str, data: bytes) -> str:
    if cos_enabled():
        await asyncio.to_thread(_cos_save, object_key, data)
        return object_key
    return await asyncio.to_thread(_local_save, object_key, data)


async def read_bytes(object_key: str) -> bytes:
    """读不到抛 FileNotFoundError（调用方据此返回 404）。

    COS 上找不到时会**回退读本地磁盘**：启用 COS 之前上传的图片都还在服务器上，
    不回退的话那批老图会全部 404（这个坑踩过一次）。
    """
    if cos_enabled():
        try:
            return await asyncio.to_thread(_cos_read, object_key)
        except FileNotFoundError:
            pass
    return await asyncio.to_thread(_local_read, object_key)


async def delete_bytes(object_key: str) -> None:
    """删除文件（best-effort，不存在则忽略）。两边都试着删一下，别留孤儿文件。"""
    targets = (_cos_delete, _local_delete) if cos_enabled() else (_local_delete,)
    for fn in targets:
        try:
            await asyncio.to_thread(fn, object_key)
        except Exception:  # noqa: BLE001  删不掉不该影响主流程
            pass

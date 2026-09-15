"""本地文件存储（首期实现，后续可替换为腾讯云 COS）。

对外只暴露 save_bytes / read_bytes 两个接口，业务层不感知底层。
"""
from pathlib import Path

_UPLOAD_DIR = Path("data/uploads")


def save_bytes(object_key: str, data: bytes) -> str:
    path = _UPLOAD_DIR / object_key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path)


def read_bytes(object_key: str) -> bytes:
    path = _UPLOAD_DIR / object_key
    if not path.exists():
        raise FileNotFoundError(object_key)
    return path.read_bytes()


def delete_bytes(object_key: str) -> None:
    """删除文件（best-effort，不存在则忽略）。"""
    path = _UPLOAD_DIR / object_key
    try:
        path.unlink()
    except FileNotFoundError:
        pass

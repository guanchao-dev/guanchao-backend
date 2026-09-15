"""小程序静态资源（图标/插图/底图）外置到服务器，减小主包体积。

资源放在 data/uploads/static/ 下，通过 GET /static/{path} 按原路径提供。
"""
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from app.core.exceptions import NotFoundError

router = APIRouter(tags=["assets"])

_STATIC_DIR = Path("data/uploads/static")

_CONTENT_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "svg": "image/svg+xml",
}


@router.get("/static/{file_path:path}")
async def get_static(file_path: str):
    """返回外置的静态资源（图片）。路径做了越权校验，只能读 _STATIC_DIR 下。"""
    target = (_STATIC_DIR / file_path).resolve()
    root = _STATIC_DIR.resolve()
    # 防目录穿越
    if root not in target.parents and target != root:
        raise NotFoundError("资源不存在")
    if not target.is_file():
        raise NotFoundError("资源不存在")
    ext = target.suffix.lstrip(".").lower()
    media_type = _CONTENT_TYPES.get(ext, "application/octet-stream")
    data = target.read_bytes()
    return Response(
        content=data,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )

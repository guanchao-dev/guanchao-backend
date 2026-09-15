"""上传：本地磁盘存储（后续可换 COS）。

提供三条链路：
- POST /uploads（multipart，本地开发直传）
- POST /uploads/credential + POST /uploads/{uploadId}/complete（对齐文档的 COS 直传协议）

游客可用：以 X-Client-Id 识别，owner_id 记 user:{id} 或 client:{id}。
"""
from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import ensure_consent, get_identity
from app.core.exceptions import InvalidFileError, NotFoundError
from app.core.response import ok
from app.core.utils import new_id
from app.db.base import get_db
from app.db.models import Upload
from app.schemas import UploadCompleteRequest, UploadCredentialRequest
from app.services import storage
from app.services.image import compress_image

router = APIRouter(tags=["uploads"])

ALLOWED_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
EXT_CONTENT_TYPES = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
MAX_SIZE = 5 * 1024 * 1024
SCENES = ("speciesGuess", "observation", "card", "community")


def _sniff_ext(data: bytes) -> str | None:
    """按文件头识别真实图片类型，兜底 wx.uploadFile 发来的 application/octet-stream。"""
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def _object_key(owner_id: str, scene: str, upload_id: str, ext: str) -> str:
    safe = owner_id.replace(":", "_")
    return f"private/{safe}/{scene}/{upload_id}.{ext}"


@router.post("/uploads")
async def upload_file(
    scene: str = Form("observation"),
    file: UploadFile = File(...),
    identity: tuple = Depends(get_identity),
    db: AsyncSession = Depends(get_db),
):
    """本地直传：直接收文件并存盘，标记 approved。"""
    user, owner_id = identity
    if user is not None:
        ensure_consent(user)
    if scene not in SCENES:
        raise InvalidFileError("scene 不合法")
    data = await file.read()
    if len(data) > MAX_SIZE:
        raise InvalidFileError("图片不能超过 5MB")
    ext = ALLOWED_TYPES.get(file.content_type or "")
    if not ext:
        ext = _sniff_ext(data) or ""
    if not ext:
        raise InvalidFileError("仅支持 jpg/png/webp")
    # 统一压缩：缩到最长边 1280px，普通图转 JPEG，减小存储与传输体积
    data, ext = compress_image(data)
    content_type = EXT_CONTENT_TYPES.get(ext, "image/jpeg")
    upload_id = new_id("up")
    object_key = _object_key(owner_id, scene, upload_id, ext)
    storage.save_bytes(object_key, data)
    db.add(
        Upload(
            id=upload_id,
            user_id=user.id if user is not None else None,
            owner_id=owner_id,
            scene=scene,
            content_type=content_type,
            ext=ext,
            object_key=object_key,
            status="approved",
        )
    )
    await db.commit()
    return ok(
        {
            "uploadId": upload_id,
            "objectKey": object_key,
            "status": "approved",
            "contentType": content_type,
        }
    )


@router.post("/uploads/credential")
async def upload_credential(
    body: UploadCredentialRequest,
    identity: tuple = Depends(get_identity),
    db: AsyncSession = Depends(get_db),
):
    user, owner_id = identity
    if user is not None:
        ensure_consent(user)
    if body.scene not in SCENES:
        raise InvalidFileError("scene 不合法")
    ext = body.ext or "jpg"
    if ext not in ("jpg", "png", "webp"):
        raise InvalidFileError("ext 不合法")
    upload_id = new_id("up")
    object_key = _object_key(owner_id, body.scene, upload_id, ext)
    db.add(
        Upload(
            id=upload_id,
            user_id=user.id if user is not None else None,
            owner_id=owner_id,
            scene=body.scene,
            content_type=body.contentType,
            ext=ext,
            object_key=object_key,
            status="processing",
        )
    )
    await db.commit()
    return ok(
        {
            "uploadId": upload_id,
            "objectKey": object_key,
            "host": "",
            "headers": {"Content-Type": body.contentType},
            "expiresIn": 300,
        }
    )


@router.post("/uploads/{upload_id}/complete")
async def upload_complete(
    upload_id: str,
    body: UploadCompleteRequest,
    identity: tuple = Depends(get_identity),
    db: AsyncSession = Depends(get_db),
):
    _, owner_id = identity
    upload = await db.get(Upload, upload_id)
    if upload is None or upload.owner_id != owner_id:
        raise NotFoundError("上传不存在")
    upload.status = "approved"
    upload.width = body.width
    upload.height = body.height
    await db.commit()
    return ok(
        {
            "uploadId": upload_id,
            "status": "approved",
            "width": body.width,
            "height": body.height,
        }
    )


@router.get("/uploads/{upload_id}/content")
async def upload_content(upload_id: str, db: AsyncSession = Depends(get_db)):
    """本地取回已上传的图片（对齐 COS 的对象读取）。"""
    upload = await db.get(Upload, upload_id)
    if upload is None:
        raise NotFoundError("上传不存在")
    try:
        data = storage.read_bytes(upload.object_key)
    except FileNotFoundError:
        raise NotFoundError("文件不存在")
    return Response(content=data, media_type=upload.content_type or "application/octet-stream")

"""上传：图片存腾讯云 COS，没配就回退本地磁盘（见 app/services/storage.py）。

提供三条链路：
- POST /uploads（multipart，直传）
- POST /uploads/credential + POST /uploads/{uploadId}/complete（对齐 COS 直传协议）
- GET /media/{key}（**只在回退本地磁盘时**才会被用到；配了 COS 就是 CDN 直链）

游客可用：以 X-Client-Id 识别，owner_id 记 user:{id} 或 client:{id}。
"""
from pathlib import Path

from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import ensure_consent, get_current_user, get_identity
from app.core.exceptions import InvalidFileError, NotFoundError
from app.core.response import ok
from app.core.utils import new_id
from app.db.base import get_db
from app.db.models import Upload, User
from app.schemas import UploadCompleteRequest, UploadCredentialRequest
from app.services import storage
from app.services.image import compress_image

router = APIRouter(tags=["uploads"])

# 本地磁盘模式下图片直链的根目录
_MEDIA_ROOT = Path("data/uploads")
_MEDIA_TYPES = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "gif": "image/gif",
}

async def as_cos_redirect(object_key: str):
    """COS 开着**且对象确实在 COS 上**时，返回 302 跳直链；否则返回 None（调用方读出来发）。

    为什么要跳：图片经过服务器转发等于同一份数据传两遍，还占服务器出口带宽。
    实测同一张图直连 COS 300~500ms、经过服务器 1000~1500ms。

    ⚠️ 两个前提，缺一个就会出事（都踩过）：
    1. 小程序后台得把 COS 域名配进「downloadFile 合法域名」—— 没配的话
       wx.downloadFile 静默失败，所有图都不显示；
    2. **必须确认对象真的在 COS 上**（storage.exists_in_cos）。只存在服务器磁盘的
       老图跳过去就是 404 —— read_bytes 本来有本地兜底，302 把它绕过去了。
       紫菜 / 帽贝 / 虾蛄 / 月螺 4 张封面就是这么没的。
    """
    from fastapi.responses import RedirectResponse

    from app.services import storage

    url = storage.public_url(object_key)
    if not url.startswith("http"):
        return None
    if not await storage.exists_in_cos(object_key):
        return None
    # 302 本身也给短缓存：同一张图重复加载时客户端不必每次都先打我们的接口
    return RedirectResponse(
        url, status_code=302, headers={"Cache-Control": "private, max-age=1800"}
    )


ALLOWED_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
EXT_CONTENT_TYPES = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
MAX_SIZE = 5 * 1024 * 1024
SCENES = ("speciesGuess", "observation", "card", "community", "spot", "aquarium")

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
    audited: str = Form(""),
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

    # AI 初审只在潮池（scene=aquarium）这条链路做：主体不是海洋生物就驳回。
    # 其它场景（识别照片、社区、宝藏点位、头像）不做 AI 判定，只进人工待审队列。
    status = "pending"
    reject_reason = ""
    reject_source = ""
    # audited=1：前端选完照片时已经调过 /aquarium/audit，这里跳过，避免重复调用模型
    if scene == "aquarium" and audited != "1":
        try:
            from app.services.ai import is_marine_creature

            marine, obj_name, _box = await is_marine_creature(data, content_type)
            if not marine:
                status = "rejected"
                reject_source = "ai"
                label = "不是海洋生物"
                if obj_name:
                    label = f"不是海洋生物（{obj_name}）"
                reject_reason = label[:64]
        except Exception:
            # 审核服务异常时放行，进人工待审
            pass
    upload_id = new_id("up")
    object_key = _object_key(owner_id, scene, upload_id, ext)
    await storage.save_bytes(object_key, data)
    db.add(
        Upload(
            id=upload_id,
            user_id=user.id if user is not None else None,
            owner_id=owner_id,
            scene=scene,
            content_type=content_type,
            ext=ext,
            object_key=object_key,
            # 新上传进审核队列；管理员在 /admin 图片审核里通过后才对公开内容可见
            status=status,
            reject_reason=reject_reason,
            reject_source=reject_source,
        )
    )
    await db.commit()
    return ok(
        {
            "uploadId": upload_id,
            "objectKey": object_key,
            "status": status,
            "rejectReason": reject_reason,
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
    upload.status = "pending"
    upload.width = body.width
    upload.height = body.height
    await db.commit()
    return ok(
        {
            "uploadId": upload_id,
            "status": "pending",
            "width": body.width,
            "height": body.height,
        }
    )


@router.get("/media/{file_path:path}")
async def media_content(file_path: str):
    """本地磁盘模式下的图片直链。

    `storage.public_url()` 在没配 COS 时返回 /media/{object_key}，就走这里。
    配了 COS 的话图片走 CDN 绝对地址，不会落到这个路由。
    """
    root = _MEDIA_ROOT.resolve()
    target = (root / file_path).resolve()
    # 目录穿越防护：resolve 之后必须还在 root 下面（与 assets.py 同一套做法）
    if not str(target).startswith(str(root)) or not target.is_file():
        raise NotFoundError("文件不存在")
    ext = target.suffix.lstrip(".").lower()
    return Response(
        content=target.read_bytes(),
        media_type=_MEDIA_TYPES.get(ext, "application/octet-stream"),
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/uploads/mine")
async def my_uploads(
    scene: str = Query("aquarium"),
    status: str = Query(""),
    appealedOnly: bool = Query(False),
    page: int = Query(1, ge=1),
    pageSize: int = Query(30, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """我上传过的图片（按场景）。潮池页面用它展示审核进度和已通过的图。

    status 可选，逗号分隔；潮池审核记录只传 rejected,appealed。
    """
    from sqlalchemy import func, select

    q = select(Upload).where(Upload.user_id == user.id, Upload.scene == scene)
    wanted = {
        s.strip()
        for s in status.split(",")
        if s.strip() in ("pending", "approved", "rejected", "appealed")
    }
    if wanted:
        q = q.where(Upload.status.in_(wanted))
    if appealedOnly:
        # 申诉记录：只显示用户点过申诉的（复核中 / 申诉成功 / 申诉未通过）
        q = q.where(Upload.appealed_at != "")
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar() or 0
    rows = (
        await db.execute(
            q.order_by(Upload.created_at.desc(), Upload.id.desc())
            .offset((page - 1) * pageSize)
            .limit(pageSize)
        )
    ).scalars().all()

    items = []
    for u in rows:
        items.append(
            {
                "uploadId": u.id,
                "status": u.status,
                "rejectReason": u.reject_reason or "",
                "rejectSource": u.reject_source or "",
                "usedAt": u.used_at or "",
                "createdAt": u.created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "width": u.width,
                "height": u.height,
                # 用签名直链：驳回/申诉中的图也能让用户自己看到（content 接口会对它们 404）
                "previewUrl": storage.public_url(u.object_key),
            }
        )
    return ok({"list": items, "page": page, "pageSize": pageSize, "total": total})


@router.post("/uploads/{upload_id}/appeal")
async def appeal_upload(
    upload_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """用户对 AI 驳回提起申诉，转人工复核。只有自己的图、且状态为 rejected 才能申诉。"""
    up = await db.get(Upload, upload_id)
    if up is None or up.user_id != user.id:
        raise NotFoundError("上传不存在")
    if up.status != "rejected":
        raise InvalidFileError("当前状态不能申诉")
    if up.reject_source == "admin":
        raise InvalidFileError("人工驳回的图片不能申诉")
    up.status = "appealed"
    up.appealed_at = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    await db.commit()
    return ok({"uploadId": upload_id, "status": "appealed"})


@router.get("/uploads/{upload_id}/content")
async def upload_content(upload_id: str, db: AsyncSession = Depends(get_db)):
    """取回已上传的图片。COS 开着就直接 302 跳直链（见 as_cos_redirect）。"""
    upload = await db.get(Upload, upload_id)
    if upload is None:
        raise NotFoundError("上传不存在")
    if upload.status in ("rejected", "appealed"):
        # 驳回 / 申诉中的图片直接 404：即使 URL 被分享出去也打不开
        raise NotFoundError("图片未通过审核")
    redirect = await as_cos_redirect(upload.object_key)
    if redirect is not None:
        return redirect
    try:
        data = await storage.read_bytes(upload.object_key)
    except FileNotFoundError:
        raise NotFoundError("文件不存在")
    return Response(content=data, media_type=upload.content_type or "application/octet-stream")

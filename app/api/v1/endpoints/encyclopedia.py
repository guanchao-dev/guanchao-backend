from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import String, cast, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.uploads import (
    ALLOWED_TYPES,
    EXT_CONTENT_TYPES,
    MAX_SIZE,
    _sniff_ext,
)
from app.core.deps import ensure_consent, get_current_user, get_optional_user
from app.core.exceptions import InvalidFileError, NotFoundError
from app.core.response import ok, paginated
from app.core.utils import new_id, to_shanghai_iso
from app.db.base import get_db
from app.db.models import Favorite, Species, SpeciesPhoto, User
from app.services import storage
from app.services.image import compress_image

router = APIRouter(tags=["encyclopedia"])


def _cover_url(species: Species) -> str:
    """图鉴封面访问路径（前端拼接 BASE_URL 使用，BASE_URL 已含 /api/v1）。"""
    return f"/encyclopedia/{species.id}/cover" if species.cover_key else ""


def _image_media_type(object_key: str) -> str:
    low = object_key.lower()
    if low.endswith(".gif"):
        return "image/gif"
    if low.endswith(".jpg") or low.endswith(".jpeg"):
        return "image/jpeg"
    if low.endswith(".webp"):
        return "image/webp"
    return "image/png"


def _species_photo_object_key(user_id: str, species_id: str, photo_id: str, ext: str) -> str:
    return f"private/{user_id}/species/{species_id}/{photo_id}.{ext}"


def _photo_url(species_id: str, photo_id: str) -> str:
    return f"/encyclopedia/{species_id}/photos/{photo_id}/content"


@router.get("/encyclopedia")
async def list_species(
    keyword: str | None = Query(None),
    category: str | None = Query(None),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    q = select(Species)
    if category:
        q = q.where(Species.category == category)
    if keyword:
        q = q.where(
            or_(
                Species.name.contains(keyword),
                cast(Species.aka, String).contains(keyword),
            )
        )
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar() or 0
    rows = (
        await db.execute(q.order_by(Species.id).offset((page - 1) * pageSize).limit(pageSize))
    ).scalars().all()
    items = [
        {
            "id": s.id,
            "name": s.name,
            "category": s.category,
            "coverUrl": _cover_url(s),
            "summary": s.summary,
            "protected": s.protected,
        }
        for s in rows
    ]
    return ok(paginated(items, page, pageSize, total))


@router.get("/encyclopedia/favorites")
async def list_favorite_species(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """我收藏的图鉴（按收藏时间倒序）。"""
    favs = (
        await db.execute(
            select(Favorite)
            .where(Favorite.user_id == user.id, Favorite.target_type == "species")
            .order_by(Favorite.created_at.desc())
        )
    ).scalars().all()
    ids = [f.target_id for f in favs]
    if not ids:
        return ok({"list": []})
    rows = (await db.execute(select(Species).where(Species.id.in_(ids)))).scalars().all()
    by_id = {s.id: s for s in rows}
    items = []
    for sid in ids:  # 保持收藏时间倒序
        s = by_id.get(sid)
        if s is None:
            continue
        items.append(
            {
                "id": s.id,
                "name": s.name,
                "category": s.category,
                "coverUrl": _cover_url(s),
                "summary": s.summary,
                "protected": s.protected,
                "favorited": True,
            }
        )
    return ok({"list": items, "total": len(items)})


@router.get("/encyclopedia/{species_id}")
async def species_detail(
    species_id: str,
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    s = await db.get(Species, species_id)
    if s is None:
        raise NotFoundError("图鉴条目不存在")
    favorited = False
    if user is not None:
        favorited = (
            await db.execute(
                select(Favorite).where(
                    Favorite.user_id == user.id,
                    Favorite.target_type == "species",
                    Favorite.target_id == species_id,
                )
            )
        ).scalar_one_or_none() is not None
    return ok(
        {
            "id": s.id,
            "name": s.name,
            "aka": s.aka or [],
            "category": s.category,
            "coverUrl": _cover_url(s),
            "look": s.look,
            "habitat": s.habitat,
            "observeTip": s.observe_tip,
            "safetyTip": s.safety_tip,
            "source": s.source,
            "reviewedAt": s.reviewed_at,
            "protected": s.protected,
            "favorited": favorited,
        }
    )


@router.post("/encyclopedia/{species_id}/favorite")
async def favorite_species(
    species_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    s = await db.get(Species, species_id)
    if s is None:
        raise NotFoundError("图鉴条目不存在")
    existing = (
        await db.execute(
            select(Favorite).where(
                Favorite.user_id == user.id,
                Favorite.target_type == "species",
                Favorite.target_id == species_id,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(
            Favorite(
                id=new_id("fav"), user_id=user.id, target_type="species", target_id=species_id
            )
        )
        await db.commit()
    return ok(None, "已收藏")


@router.delete("/encyclopedia/{species_id}/favorite")
async def unfavorite_species(
    species_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await db.execute(
        delete(Favorite).where(
            Favorite.user_id == user.id,
            Favorite.target_type == "species",
            Favorite.target_id == species_id,
        )
    )
    await db.commit()
    return ok(None, "已取消收藏")


@router.get("/encyclopedia/{species_id}/cover")
async def species_cover(species_id: str, db: AsyncSession = Depends(get_db)):
    """返回官方图鉴封面图（本地直读）。"""
    s = await db.get(Species, species_id)
    if s is None or not s.cover_key:
        raise NotFoundError("图鉴图片不存在")
    try:
        data = storage.read_bytes(s.cover_key)
    except FileNotFoundError:
        raise NotFoundError("图片文件不存在")
    return Response(content=data, media_type=_image_media_type(s.cover_key))


@router.post("/encyclopedia/{species_id}/photos")
async def upload_species_photo(
    species_id: str,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """用户上传照片到图鉴物种下（仅自己可见，不进公共图鉴）。"""
    s = await db.get(Species, species_id)
    if s is None:
        raise NotFoundError("图鉴条目不存在")
    ensure_consent(user)
    data = await file.read()
    if len(data) > MAX_SIZE:
        raise InvalidFileError("图片不能超过 5MB")
    ext = ALLOWED_TYPES.get(file.content_type or "")
    if not ext:
        ext = _sniff_ext(data) or ""
    if not ext:
        raise InvalidFileError("仅支持 jpg/png/webp")
    data, ext = compress_image(data)
    content_type = EXT_CONTENT_TYPES.get(ext, "image/jpeg")
    photo_id = new_id("sph")
    object_key = _species_photo_object_key(user.id, species_id, photo_id, ext)
    storage.save_bytes(object_key, data)
    photo = SpeciesPhoto(
        id=photo_id,
        user_id=user.id,
        species_id=species_id,
        upload_id="",
        object_key=object_key,
        content_type=content_type,
    )
    db.add(photo)
    await db.commit()
    return ok(
        {
            "photoId": photo_id,
            "speciesId": species_id,
            "coverUrl": _photo_url(species_id, photo_id),
            "createdAt": to_shanghai_iso(photo.created_at),
        }
    )


@router.get("/encyclopedia/{species_id}/photos")
async def list_species_photos(
    species_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """当前用户为该物种上传的照片列表（仅自己可见）。"""
    rows = (
        await db.execute(
            select(SpeciesPhoto)
            .where(SpeciesPhoto.user_id == user.id, SpeciesPhoto.species_id == species_id)
            .order_by(SpeciesPhoto.created_at.desc())
        )
    ).scalars().all()
    items = [
        {
            "photoId": p.id,
            "speciesId": p.species_id,
            "coverUrl": _photo_url(species_id, p.id),
            "createdAt": to_shanghai_iso(p.created_at),
        }
        for p in rows
    ]
    return ok({"list": items, "total": len(items)})


@router.get("/encyclopedia/{species_id}/photos/{photo_id}/content")
async def species_photo_content(species_id: str, photo_id: str, db: AsyncSession = Depends(get_db)):
    """返回用户上传的照片（photo_id 不可猜测，供 <image> 直接加载）。"""
    photo = await db.get(SpeciesPhoto, photo_id)
    if photo is None or photo.species_id != species_id:
        raise NotFoundError("图片不存在")
    try:
        data = storage.read_bytes(photo.object_key)
    except FileNotFoundError:
        raise NotFoundError("图片文件不存在")
    return Response(content=data, media_type=photo.content_type or "application/octet-stream")


@router.delete("/encyclopedia/{species_id}/photos/{photo_id}")
async def delete_species_photo(
    species_id: str,
    photo_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    photo = await db.get(SpeciesPhoto, photo_id)
    if photo is None or photo.user_id != user.id:
        raise NotFoundError("图片不存在")
    object_key = photo.object_key
    await db.delete(photo)
    await db.commit()
    if object_key:
        storage.delete_bytes(object_key)
    return ok(None, "已删除")

"""图片压缩：上传的图片统一缩到合理尺寸再存盘，减小存储与传输体积。

规则：
- 最长边超过 MAX_SIDE 就等比缩小；
- 普通照片转 JPEG（quality 82）；
- 透明 PNG 保留透明（缩尺寸、仍存 PNG，避免黑底）；
- 动图（GIF/动画）保留原样；
- 已经够小（尺寸 ≤ MAX_SIDE 且体积 < SMALL_SIZE）不再二次压缩。
"""
import io

from PIL import Image

MAX_SIDE = 1280
JPEG_QUALITY = 82
SMALL_SIZE = 200_000  # 字节


def _sniff_ext(data: bytes) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return "jpg"


def _fit(img: Image.Image, max_side: int) -> Image.Image:
    w, h = img.size
    if max(w, h) > max_side:
        scale = max_side / max(w, h)
        img = img.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
    return img


def compress_image(
    image_bytes: bytes, max_side: int = MAX_SIDE, quality: int = JPEG_QUALITY
) -> tuple[bytes, str]:
    """压缩图片，返回 (bytes, ext)。ext ∈ jpg/png/gif/webp。"""
    try:
        img = Image.open(io.BytesIO(image_bytes))
    except Exception:
        return image_bytes, _sniff_ext(image_bytes)

    fmt = (img.format or "").upper()
    orig_ext = _sniff_ext(image_bytes)

    # 动图保留原样
    if fmt == "GIF" or getattr(img, "is_animated", False):
        return image_bytes, "gif"

    w, h = img.size
    already_small = max(w, h) <= max_side and len(image_bytes) < SMALL_SIZE

    # 透明图：保留透明，只缩尺寸
    if img.mode in ("RGBA", "LA") or (fmt == "PNG" and "transparency" in (img.info or {})):
        if already_small:
            return image_bytes, "png"
        out = _fit(img.convert("RGBA"), max_side)
        buf = io.BytesIO()
        out.save(buf, format="PNG", optimize=True)
        return buf.getvalue(), "png"

    # 普通照片
    if already_small:
        return image_bytes, orig_ext
    out = _fit(img.convert("RGB"), max_side)
    buf = io.BytesIO()
    out.save(buf, format="JPEG", quality=quality)
    return buf.getvalue(), "jpg"

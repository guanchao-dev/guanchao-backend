"""AI 抠图：把用户照片里的生物分割出来，输出透明 PNG。

模型用 rembg 的 u2netp（约 4.7MB，CPU 可跑）。
- 模型默认放 `后端/models/u2netp/u2netp.onnx`，通过 U2NET_HOME 指过去；
- 文件不存在时直接报错，不在请求里等下载（线上提前放好模型）；
- 抠完裁到 alpha 外接框，再加一圈黑/白贴纸边，和浏览器原型保持一致。
"""
import base64
import io
import json
import logging
import os
import threading
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps

from app.core.config import settings
from app.core.exceptions import AiUnavailableError

logger = logging.getLogger(__name__)

# 千问图像编辑：同步图生图接口。这个 Key 不支持异步（X-DashScope-Async 会 403），
# 单张 turbo 约 10 秒、pro 约 25 秒，所以端点里必须用 asyncio.to_thread。
_QWEN_EDIT_URL = (
    "https://dashscope.aliyuncs.com/api/v1/services/aigc/"
    "multimodal-generation/generation"
)
# 2.1 系列会根据提示词自动决定输出普通图还是带 alpha 的 PNG；
# 必须把「透明、alpha=0、不要纯色背景」写死，否则有一半概率回白底。
_QWEN_PROMPT = (
    "只做一件事：把照片背景完全移除，保留画面中的海洋生物主体。"
    "主体的形状、姿态、颜色、纹理必须与原图完全一致，不要重绘、不要美化、"
    "不要补全、不要改变细节。如果原图里有显示器边框、桌面、手指、阴影或倒影，"
    "也全部去掉。导出 PNG，背景像素必须完全透明（alpha=0），"
    "主体之外不要有任何白色、灰色、彩色背景。"
)

_ROOT = Path(__file__).resolve().parents[2]
_MODEL_DIR = _ROOT / "models"
_MODEL_DIR.mkdir(parents=True, exist_ok=True)
# rembg 会在 U2NET_HOME 下找 models/u2netp/u2netp.onnx
os.environ.setdefault("U2NET_HOME", str(_ROOT))

# numba/pymatting 默认把编译缓存写进 site-packages，服务用户 www-data 没权限，
# 会导致 rembg 导入失败。必须给它一个可写缓存目录。
_NUMBA_CACHE = _ROOT / "data" / "numba_cache"
try:
    _NUMBA_CACHE.mkdir(parents=True, exist_ok=True)
except Exception:
    _NUMBA_CACHE = Path("/tmp/guanchao_numba_cache")
    _NUMBA_CACHE.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("NUMBA_CACHE_DIR", str(_NUMBA_CACHE))

_session = None
_session_lock = threading.Lock()


def _get_session():
    global _session
    if _session is not None:
        return _session
    with _session_lock:
        if _session is not None:
            return _session
        # 优先用完整版 u2net（176MB，边缘和整体性好得多），没部署时退回 u2netp
        chosen = None
        for name in ("u2net", "u2netp"):
            if (_MODEL_DIR / name / f"{name}.onnx").exists():
                chosen = name
                break
        if chosen is None:
            raise AiUnavailableError("AI 抠图模型未部署，请把 models/u2net/u2net.onnx 放到服务器")
        try:
            from rembg import new_session
        except Exception as exc:  # 依赖没装
            raise AiUnavailableError("AI 抠图服务未安装，请稍后再试或手动描边") from exc
        try:
            _session = new_session(chosen)
        except Exception as exc:  # 模型加载失败
            raise AiUnavailableError("AI 抠图模型加载失败，请稍后再试或手动描边") from exc
    return _session


def warmup() -> None:
    """后台预热：提前加载模型和 numba 缓存，避免第一个用户请求等几十秒。"""
    try:
        _get_session()
    except Exception:
        # 预热失败不影响服务；真正请求时会再报一次明确的错误
        pass


def _prepare_input(data: bytes, box: list | None = None) -> bytes:
    """手机照片先转正、按主体框裁一刀、缩到最长边 1280。

    box 是识别阶段给的主体外接框（归一化 x,y,w,h）。先裁到主体、留 8% 边，
    分割模型看到的背景更少、主体更大，边缘和准确率都会好一些。
    """
    try:
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img).convert("RGB")
    except Exception as exc:
        raise AiUnavailableError("图片读取失败，请换一张照片") from exc

    if box:
        try:
            w, h = img.size
            bx, by, bw, bh = [float(v) for v in box]
            mx, my = bw * 0.08, bh * 0.08
            left = max(0, int((bx - mx) * w))
            top = max(0, int((by - my) * h))
            right = min(w, int((bx + bw + mx) * w))
            bottom = min(h, int((by + bh + my) * h))
            if right - left >= 32 and bottom - top >= 32:
                img = img.crop((left, top, right, bottom))
        except Exception:
            # box 不合法就按整图处理
            pass

    max_side = 1280
    w, h = img.size
    if max(w, h) > max_side:
        scale = max_side / max(w, h)
        img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


def _refine_alpha(img: Image.Image) -> Image.Image:
    """抠图后的轻量复核：只去掉明显是背景的零散小块。

    原则是「只去背景，不动主体」：
    - 不补洞（腿缝、钳子间的空隙本来就是背景）；
    - 不做形态学闭/开运算（会啃边、把细腿抹掉）；
    - 不改模型给的 alpha（保留抗锯齿），只把被判定为背景的像素置 0。

    只有「又小又离主体远」的连通块才会被删，眼睛、鳍、钳子这类贴主体的零件全部保留。
    """
    import numpy as np
    from scipy import ndimage as ndi

    alpha = np.array(img.getchannel("A"))
    if alpha.size == 0:
        raise AiUnavailableError("没有识别到主体，请换一张背景更干净的照片")

    mask = alpha > 32
    area0 = int(mask.sum())
    if area0 < 64:
        raise AiUnavailableError("没有识别到主体，请换一张背景更干净的照片")

    labeled, n = ndi.label(mask)
    if n > 1:
        sizes = ndi.sum(mask, labeled, range(1, n + 1))
        biggest_i = int(np.argmax(sizes)) + 1
        biggest = float(sizes[biggest_i - 1])
        big = labeled == biggest_i
        # 离主体最近像素的距离：贴主体的零件（眼睛/鳍）距离很小
        dist = ndi.distance_transform_edt(~big)
        gap = max(16.0, max(mask.shape) * 0.06)
        min_dist = ndi.minimum(dist, labeled, np.arange(1, n + 1))

        keep = np.zeros(n + 1, dtype=bool)
        # 够大的块保留
        keep[1:] = sizes >= max(biggest * 0.01, 32)
        # 小但贴着主体的块也保留（眼睛、钳尖、鳍）
        for i in range(1, n + 1):
            if sizes[i - 1] >= 32 and min_dist[i - 1] <= gap:
                keep[i] = True
        mask = keep[labeled]

    new_alpha = np.where(mask, alpha, 0).astype("uint8")

    out = img.copy()
    out.putalpha(Image.fromarray(new_alpha, mode="L"))
    return out


def _sticker(img: Image.Image) -> Image.Image:
    """裁到主体外接框，再加黑外圈 + 白内圈。"""
    alpha = img.getchannel("A")
    bbox = alpha.getbbox()
    if bbox is None:
        raise AiUnavailableError("没有识别到主体，请换一张背景更干净的照片")
    img = img.crop(bbox)

    w, h = img.size
    edge = max(6, min(18, int(round(max(w, h) * 0.025))))
    edge = max(6, edge - (edge % 2))  # 偶数，保证白圈滤镜尺寸为奇数
    pad = edge * 2

    padded = Image.new("RGBA", (w + pad * 2, h + pad * 2), (0, 0, 0, 0))
    padded.alpha_composite(img, (pad, pad))
    alpha = padded.getchannel("A")

    dark_alpha = alpha.filter(ImageFilter.MaxFilter(edge * 2 + 1))
    white_alpha = alpha.filter(ImageFilter.MaxFilter(max(3, edge) + 1))

    dark = Image.new("RGBA", padded.size, (28, 28, 28, 0))
    dark.putalpha(dark_alpha)
    white = Image.new("RGBA", padded.size, (255, 255, 255, 0))
    white.putalpha(white_alpha)

    out = Image.new("RGBA", padded.size, (0, 0, 0, 0))
    out.alpha_composite(dark)
    out.alpha_composite(white)
    out.alpha_composite(padded)
    return out


def _finalize(result: bytes) -> tuple[bytes, int, int]:
    """把任意抠图结果统一成：清残留 → 加贴纸边 → 缩到 1000px 的 PNG。"""
    try:
        img = Image.open(io.BytesIO(result)).convert("RGBA")
        # 第二道复核：清残留 / 补小洞 / 平滑边缘
        img = _refine_alpha(img)
        img = _sticker(img)
    except AiUnavailableError:
        raise
    except Exception as exc:
        raise AiUnavailableError("AI 抠图结果处理失败，请稍后再试") from exc

    max_side = 1000
    if max(img.size) > max_side:
        scale = max_side / max(img.size)
        img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue(), img.width, img.height


def cutout_png(data: bytes, box: list | None = None) -> tuple[bytes, int, int]:
    """同步执行本地 AI 抠图（u2net），返回 (PNG bytes, width, height)。

    这个函数是 CPU 密集的，FastAPI 端点里要用 asyncio.to_thread 调用。
    """
    session = _get_session()
    prepared = _prepare_input(data, box)
    try:
        from rembg import remove
        result = remove(
            prepared,
            session=session,
            alpha_matting=False,
            post_process_mask=True,
        )
    except AiUnavailableError:
        raise
    except Exception as exc:
        raise AiUnavailableError("AI 抠图失败，请稍后再试或手动描边") from exc

    return _finalize(result)


def _qwen_cutout_once(prepared: bytes) -> bytes:
    """调用一次千问图像编辑，返回模型输出的 PNG 字节。"""
    if not settings.dashscope_api_key:
        raise AiUnavailableError("AI 精修未配置，请稍后再试")

    body = {
        "model": settings.qwen_cutout_model,
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "image": "data:image/jpeg;base64,"
                            + base64.b64encode(prepared).decode()
                        },
                        {"text": _QWEN_PROMPT},
                    ],
                }
            ]
        },
        # 关掉提示词改写：我们只要「去背景」，改写反而会加戏。
        "parameters": {"n": 1, "watermark": False, "prompt_extend": False},
    }
    headers = {
        "Authorization": f"Bearer {settings.dashscope_api_key}",
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(
        _QWEN_EDIT_URL,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=settings.qwen_cutout_timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        logger.warning("qwen cutout HTTP %s: %s", exc.code, detail)
        if exc.code == 429:
            raise AiUnavailableError("AI 精修排队的同学太多了，请稍后再试") from exc
        raise AiUnavailableError("AI 精修服务繁忙，请稍后再试") from exc
    except Exception as exc:
        logger.warning("qwen cutout failed: %s", exc)
        raise AiUnavailableError("AI 精修超时了，请稍后再试") from exc

    try:
        content = data["output"]["choices"][0]["message"]["content"]
        url = next(
            item["image"]
            for item in content
            if isinstance(item, dict) and item.get("image")
        )
    except (KeyError, IndexError, TypeError, StopIteration) as exc:
        logger.warning("qwen cutout bad response: %s", str(data)[:300])
        raise AiUnavailableError("AI 精修没有返回图片，请重试") from exc

    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            return resp.read()
    except Exception as exc:
        logger.warning("qwen cutout download failed: %s", exc)
        raise AiUnavailableError("AI 精修结果下载失败，请重试") from exc


def _alpha_stats(png: bytes) -> tuple[float, float]:
    """返回 (近全透明像素占比, 近不透明像素占比)，用来判断模型有没有真出 alpha。"""
    img = Image.open(io.BytesIO(png)).convert("RGBA")
    hist = img.getchannel("A").histogram()
    total = img.width * img.height
    if total <= 0:
        return 0.0, 0.0
    return sum(hist[:8]) / total, sum(hist[248:]) / total


def cutout_png_qwen(data: bytes, box: list | None = None) -> tuple[bytes, int, int]:
    """千问图像编辑「AI 精修」：用户主动触发，失败不静默回退本地。

    2.1 系列的透明输出不是 100% 稳定（同一提示词偶尔回白底），所以校验 alpha，
    不合格重试一次，仍不合格就报错，让前端保留原来的贴纸。
    """
    prepared = _prepare_input(data, box)
    attempts = 1 + max(0, settings.qwen_cutout_retries)
    last_reason = "AI 没返回透明背景，请重试"

    for attempt in range(attempts):
        result = _qwen_cutout_once(prepared)
        transparent, opaque = _alpha_stats(result)
        # 正常结果：大部分背景透明、主体保留；全透明或白底都算失败。
        if transparent >= 0.02 and opaque >= 0.005:
            return _finalize(result)
        logger.warning(
            "qwen cutout not transparent (attempt %s/%s): transparent=%.3f opaque=%.3f",
            attempt + 1,
            attempts,
            transparent,
            opaque,
        )
        if transparent >= 0.98:
            last_reason = "AI 把主体也抠没了，请换一张照片或手描"
        else:
            last_reason = "AI 没返回透明背景，请重试"

    raise AiUnavailableError(last_reason)

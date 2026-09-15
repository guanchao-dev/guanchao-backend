"""通义千问（DashScope）客户端 + AI 能力实现。

- 文本生成用 `qwen_text_model`（默认 qwen-plus），视觉理解用 `qwen_vl_model`（默认 qwen-vl-max）。
- 走 OpenAI 兼容模式（dashscope_base_url）。
- 潮汐建议 / 图鉴卡失败时优雅降级为规则模板；物种猜测失败时抛 AiUnavailableError（50003，与文档一致）。
"""
import asyncio
import base64
import json
import re
import time
from datetime import datetime

import httpx

from app.core.config import settings
from app.core.exceptions import AiUnavailableError


def _extract_content(data: dict) -> str:
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


async def _chat(
    messages: list,
    model: str,
    temperature: float = 0.6,
    max_tokens: int = 800,
    timeout: float = 12,
) -> str:
    url = f"{settings.dashscope_base_url}/chat/completions"
    payload = {"model": model, "messages": messages, "temperature": temperature}
    if max_tokens:
        payload["max_tokens"] = max_tokens
    headers = {
        "Authorization": f"Bearer {settings.dashscope_api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, json=payload, headers=headers)
    resp.raise_for_status()
    return _extract_content(resp.json())


def _repair_llm_json(text: str) -> str:
    """修复 LLM 输出 JSON 的常见格式错误（多余引号），使 json.loads 能通过。

    qwen-vl 偶尔会在数组元素之间 / 对象结尾多输出一个双引号，例如：
        [{...},"{"..."}" ]  —— 中间的 ,"{ 和结尾的 }" 都是多余的引号。
    """
    text = re.sub(r',"(\{)', r',\1', text)  # ,"{ -> ,{
    text = re.sub(r'\}"(?=\s*[,\]])', '}', text)  # }" -> }（后跟 ] 或 ,）
    return text


# 物品切分点：两个 prompt 都要求每件物品必有 label，且它不会出现在候选内部，
# 所以按它切不会把一件物品切成两段（这正是把 label 和 position 都当切分点会踩的坑）。
_ITEM_MARK_RE = re.compile(r'"(?:label|position)"\s*:')


def _salvage_candidates(seg: str) -> list[dict]:
    """从一段文本里抠出候选数组（按 "name" 切分，每段再抠自己的字段）。"""
    cands = []
    for piece in re.split(r'(?="name"\s*:)', seg)[1:]:
        piece = piece.split("]")[0]  # 截到本数组结束，别串进下一件物品
        head = re.match(r'\s*"name"\s*:\s*"([^"]*)"', piece)
        if not head:
            continue
        cand: dict = {"name": head.group(1)}
        for key in ("latinName", "category", "reason", "speciesId", "habitat"):
            m = re.search(rf'"{key}"\s*:\s*"([^"]*)"', piece)
            if m:
                cand[key] = m.group(1)
        m = re.search(r'"probability"\s*:\s*([0-9]*\.?[0-9]+)', piece)
        if m:
            cand["probability"] = float(m.group(1))
        cands.append(cand)
    return cands


def _salvage_items(body: str) -> list[dict]:
    """按「物品」粒度抠 items（新结构兜底）。

    物种物品靠段内的 candidates 数组识别，垃圾物品的字段直接铺在物品层，
    两者用 `"candidates" in seg` 区分，不会互相污染。
    """
    marks = list(_ITEM_MARK_RE.finditer(body))
    if not marks:
        return []
    out = []
    for i, mk in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(body)
        seg = body[mk.start() : end]
        item: dict = {}
        m = re.search(r'"(?:label|position)"\s*:\s*"([^"]*)"', seg)
        if m:
            item["label"] = m.group(1)
        m = re.search(r'"count"\s*:\s*(\d+)', seg)
        if m:
            item["count"] = int(m.group(1))
        if '"candidates"' in seg:
            # 物种物品：字段都在候选里，物品层只留 label/count/candidates
            cands = _salvage_candidates(seg)
            if cands:
                item["candidates"] = cands
        else:
            # 垃圾物品：字段直接铺在物品层
            for key in ("name", "category", "reason", "tip", "hazardNote"):
                m = re.search(rf'"{key}"\s*:\s*"([^"]*)"', seg)
                if m:
                    item[key] = m.group(1)
            m = re.search(r'"probability"\s*:\s*([0-9]*\.?[0-9]+)', seg)
            if m:
                item["probability"] = float(m.group(1))
        if item.get("name") or item.get("candidates"):
            out.append(item)
    return out


def _salvage_fields(body: str) -> dict:
    """兜底：整体不是合法 JSON 时，按字段名宽松地逐个抠出来。

    qwen-vl 除了多输出引号，偶尔还会**漏掉数组元素开头的花括号**，例如：
        "candidates":[{...},"name":"塑料盒","category":"recyclable",...]
    第二个候选丢了 `{`。这种结构性破坏用正则补不稳（要判断数组/对象上下文、
    还要补上对应的 `}`），但键值对本身通常是对的，于是直接按字段名抠。

    注意：只在 json.loads 两条路都失败时才走到这里，正常输出不受影响。
    """
    out: dict = {}

    for key in ("isTrash", "isSeaCreature"):
        m = re.search(rf'"{key}"\s*:\s*(true|false)', body, re.I)
        if m:
            out[key] = m.group(1).lower() == "true"

    items = _salvage_items(body)
    if items:
        out["items"] = items
    else:
        # 模型退回老结构（候选平铺在顶层，没有 label）时的兜底
        cands = _salvage_candidates(body)
        if cands:
            out["candidates"] = cands

    # 这些键只出现在 items 之外，取第一处即可
    for key in ("objectName", "observeTip", "safetyTip", "message"):
        m = re.search(rf'"{key}"\s*:\s*"([^"]*)"', body)
        if m:
            out[key] = m.group(1)
    return out


def _parse_json(text: str) -> dict:
    """从模型输出里稳健地抠出 JSON 对象。"""
    if not text:
        return {}
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
    s, e = text.find("{"), text.rfind("}")
    if s == -1 or e <= s:
        return {}
    body = text[s : e + 1]
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        pass
    try:
        return json.loads(_repair_llm_json(body))
    except json.JSONDecodeError:
        pass
    # 最后兜底：结构性修复也救不回来（如漏花括号）时，按字段名抠
    return _salvage_fields(body)


def _image_data_url(image_bytes: bytes, content_type: str) -> str:
    b64 = base64.b64encode(image_bytes).decode()
    return f"data:{content_type or 'image/jpeg'};base64,{b64}"


def _compress_for_vl(image_bytes: bytes, max_side: int = 1280) -> bytes:
    """把图片缩到视觉模型适合的尺寸（最长边 max_side），再转 JPEG。

    qwen-vl 默认 max_pixels 约 131 万，拍照原图动辄上千万像素会超限或超时，
    缩到 1280 边长（约 100 万像素）既保识别清晰度又稳定。
    """
    import io

    from PIL import Image

    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    w, h = img.size
    if max(w, h) > max_side:
        scale = max_side / max(w, h)
        img = img.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


async def _advice_text(
    tide: dict, weather: dict, spot: dict, beach: dict, leave_before: str | None = None
) -> tuple[str, str]:
    points = "、".join(f"{p['time']}({p['type']})" for p in tide["points"])
    win = beach.get("bestWindow") or {}
    best_line = ""
    if win.get("lowTideTime"):
        best_line = (
            f"- 最佳赶海时段：{win.get('start', '')}-{win.get('end', '')}"
            f"（低潮 {win.get('lowTideTime', '')} 前后）\n"
        )
    # 把离场时间喂给模型并要求直接用它。否则它会自己拿高潮时间推算
    # （实测出现过界面写 12:53、正文写 19:02 的情况），和界面上的时间对不上。
    leave_line = ""
    if leave_before:
        leave_line = (
            f"- **离场时间：{leave_before}**。写「什么时候该离开」时**只能用它**，"
            f"不要用高潮时间或其他时间自行推算。\n"
        )
    prompt = (
        f"今天{spot.get('name', '海边')}（{spot.get('city', '')}）的潮汐与天气：\n"
        f"- 当前潮高约 {tide['currentHeightM']} 米，趋势 {tide['trend']}（rising=涨潮 falling=退潮）\n"
        f"- 潮汐点：{points}\n"
        f"- 天气：{weather['text']}，{weather['tempC']}℃，{weather['windDir']}风 {weather['windScale']} 级，{weather['waveHint']}\n"
        f"- 点位适龄提示：{spot.get('age_hint', '')}；安全提示：{'、'.join(spot.get('safety_tags', []))}\n"
        f"- 赶海判断：{beach.get('label', '')}，{beach.get('reason', '')}，{beach.get('goAdvice', '')}\n"
        f"{best_line}{leave_line}\n"
        f"请为赶海的小朋友（小学中高年级）写一段出门建议。\n"
        f"**body 只写两件事**：\n"
        f"1. 当前潮汐状态（在涨潮还是退潮、潮高大约多少）；\n"
        f"2. 什么时候适合赶海、什么时候该离开（请用上面给的具体时间点）。\n"
        f"**不要写**：天气、气温、穿衣、装备、以及泛泛的安全叮嘱"
        f"（这些前端另有位置展示，写进来会被过滤掉）。\n"
        f"要求：2 到 3 句短句、语气温和、不恐吓、不鼓励冒险，"
        f"不要出现「绝对安全」「保证适合赶海」。只输出 JSON："
        f'{{"headline":"一句话标题，20字以内","body":"2到3句话，只讲潮汐状态与赶海时机"}}'
    )
    raw = await _chat(
        [
            {
                "role": "system",
                "content": "你是「追潮记」小程序的海洋科普助手，回答只输出 JSON，不要输出任何多余文字。",
            },
            {"role": "user", "content": prompt},
        ],
        model=settings.qwen_text_model,
        temperature=0.6,
        max_tokens=300,
    )
    data = _parse_json(raw)
    headline = str(data.get("headline") or "").strip()
    body = str(data.get("body") or "").strip()
    if not headline or not body:
        raise AiUnavailableError("AI 返回不完整")
    return headline, body


_advice_cache: dict[str, tuple[float, dict]] = {}
_ADVICE_TTL = 3600  # 秒：同一地点同一小时内复用 AI 建议，避免频繁重新生成


async def tide_advice(tide: dict, weather: dict, spot: dict, now: datetime) -> dict:
    """潮汐出门建议。AI 融合潮汐/天气/赶海时机生成 headline/body；失败降级为规则模板。"""
    from app.services.tide import build_beachcombing_hint, build_fallback_advice  # 局部导入避免循环依赖

    fallback = build_fallback_advice(tide, weather, now)
    beach = build_beachcombing_hint(tide, now)

    # 把赶海时机融合进规则兜底正文（降级 / AI 失败时也能给出赶海信息）
    if beach.get("goAdvice"):
        fallback["body"] = f"{fallback['body']} {beach['goAdvice']}"

    # 凌晨/数据过期等硬性不适合时段：直接返回，不调用 AI，避免 AI 给出矛盾的赶海建议
    if beach.get("blocked"):
        return {
            "headline": "现在不适合赶海",
            "body": beach["goAdvice"],
            "suitableNow": False,
            "suitableForLowerGrade": True,
            "leaveBefore": None,
            "nextChange": {},
            "weatherLine": f"今天{weather['text']}、{weather['windDir']}风 {weather['windScale']} 级、{weather['waveHint']}。",
            "safetyLine": "请由大人陪同；不要独自下水；以现场警示和官方预警为准。",
            "disclaimer": "潮汐与天气仅供参考，出海或近水活动请以海洋预报和现场管理为准。",
            "generatedBy": "rule",
            "fallback": True,
        }

    if not settings.dashscope_api_key:
        return fallback

    # 缓存：同一地点同一小时内复用 AI 建议，避免每次刷新文案都变
    cache_key = f"{spot.get('name', '')}:{now.strftime('%Y-%m-%dT%H')}"
    cached = _advice_cache.get(cache_key)
    if cached is not None and cached[0] > time.monotonic():
        return cached[1]

    try:
        headline, body = await _advice_text(
            tide, weather, spot, beach, fallback.get("leaveBefore")
        )
        advice = dict(fallback)
        advice["headline"] = headline
        advice["body"] = body
        advice["generatedBy"] = "ai"
        advice["fallback"] = False
        _advice_cache[cache_key] = (time.monotonic() + _ADVICE_TTL, advice)
        return advice
    except Exception:
        return fallback


# 低于这个把握度的候选不再展示。0.35 是 _confidence_label 里「也有可能」的下界，
# 再往下就是「不太像 / 建议对照图鉴」——摆给用户看只会让人困惑。
_KEEP_MIN_PROB = 0.35


def _confidence_label(c: float) -> str:
    if c >= 0.6:
        return "比较像"
    if c >= 0.35:
        return "也有可能"
    if c >= 0.15:
        return "不太像"
    return "建议对照图鉴"


def _probability_text(p: float) -> str:
    return f"{int(round(p * 100))}%"


# 泛称 / 集合名词黑名单：AI 有时会偷懒给这类名字，一律替换为「认不出具体种类」
_GENERIC_NAMES = {
    "潮池小鱼", "小螃蟹", "小鱼", "小蟹", "小虾", "小贝", "蟹类", "贝类", "螺类", "鱼类",
    "海藻", "贝壳", "某种鱼", "某种蟹", "某种贝", "某种海藻", "不认识的鱼", "不认识的生物",
    "小型鱼类", "小型蟹类", "潮间带生物", "海洋生物", "未知物种", "不清楚", "看不出来",
}
_GENERIC_SUFFIX = ("类的", "某种", "不明")


def _is_generic_name(name: str) -> bool:
    n = (name or "").strip()
    if not n:
        return True
    if n in _GENERIC_NAMES:
        return True
    return any(s in n for s in _GENERIC_SUFFIX)


def _habitat_ok(habitat: str, coastal: bool) -> bool:
    """按用户所在地过滤：海边不要淡水/陆生物种，内陆不要海洋物种。"""
    h = (habitat or "").strip().lower()
    if h not in ("sea", "fresh", "land"):
        return True  # AI 没标就不过滤，宁可多给也不误杀
    if coastal:
        return h == "sea"
    return h != "sea"


def _normalize_guess(data: dict, name_index: dict, coastal: bool = True) -> list[dict]:
    """把 AI 输出整理成 candidates。

    - 优先比对图鉴：能对上名录的回填 speciesId / inEncyclopedia=True；
    - 按用户所在地过滤掉不可能出现的物种（海边不出现淡水鱼等）；
    - 每个候选带 probability（0~1）与 probabilityText（百分比文案）。
    """
    valid_ids = set(name_index.values())
    raw_cands = data.get("candidates") if isinstance(data, dict) else None
    if not isinstance(raw_cands, list):
        raw_cands = []
    out = []
    for c in raw_cands[:5]:
        if not isinstance(c, dict):
            continue
        name = str(c.get("name") or "").strip()
        if not name:
            continue
        try:
            prob = float(c.get("probability", c.get("confidence")) or 0)
        except (TypeError, ValueError):
            prob = 0.0
        prob = max(0.0, min(1.0, prob))
        # 图鉴匹配：优先用模型给的 speciesId，其次按名称/别名反查
        sid = c.get("speciesId")
        if sid not in valid_ids:
            sid = name_index.get(name)
        reason = str(c.get("reason") or "")[:40] or "依据外观特征判断。"
        # 地理过滤：图鉴里的都是潮间带海洋生物，只在「海边」时保留
        habitat = str(c.get("habitat") or "")
        if sid and not coastal:
            continue
        if not _habitat_ok(habitat, coastal):
            continue
        # 兜底：AI 万一还是给了泛称（如「潮池小鱼」），替换成「认不出具体种类」
        if not sid and _is_generic_name(name):
            out.append(
                {
                    "name": "认不出具体种类",
                    "latinName": "",
                    "probability": 0.0,
                    "reason": reason,
                    "speciesId": None,
                    "inEncyclopedia": False,
                    "habitat": habitat,
                }
            )
            continue
        out.append(
            {
                "name": name,
                "latinName": str(c.get("latinName") or "").strip()[:60],
                "probability": round(prob, 2),
                "reason": reason,
                "speciesId": sid,
                "inEncyclopedia": bool(sid),
                "habitat": habitat,
            }
        )

    out.sort(key=lambda x: x["probability"], reverse=True)
    # 特征明显不符的候选不摆给用户看。至少保留最可能的那个，避免整项变空。
    strong = [c for c in out if c["probability"] >= _KEEP_MIN_PROB]
    out = strong or out[:1]

    ranked = []
    for i, c in enumerate(out):
        ranked.append(
            {
                "rank": i + 1,
                "name": c["name"],
                "latinName": c.get("latinName", ""),
                "probability": c["probability"],
                "probabilityText": _probability_text(c["probability"]),
                # 兼容旧字段
                "confidence": c["probability"],
                "confidenceLabel": _confidence_label(c["probability"]),
                "reason": c["reason"],
                # 图鉴匹配
                "speciesId": c.get("speciesId"),
                "inEncyclopedia": c.get("inEncyclopedia", False),
                "habitat": c.get("habitat", ""),
                "wikiPath": f"/encyclopedia/{c['speciesId']}" if c.get("speciesId") else None,
            }
        )
    return ranked


# 主体明显属于海洋生物的词。模型一边说出这些名字、一边又判「不是生物」时，
# 就是自相矛盾（典型场景：翻拍电脑屏幕上的螃蟹照片被判成「非生物场景」）。
_MARINE_HINTS = (
    "蟹", "螺", "贝", "蛤", "蚌", "蛏", "蚝", "蚶", "鱼", "虾", "海星", "海葵",
    "海胆", "海参", "海藻", "水母", "章鱼", "鱿鱼", "乌贼", "藤壶", "沙虫", "海马",
)


def _looks_like_marine(name: str) -> bool:
    return any(h in (name or "") for h in _MARINE_HINTS)


def _flatten_raw_candidates(data: dict) -> list:
    """把 items 各项的候选摊平成一个大列表（兼容老的顶层 candidates 结构）。

    只用于「一个都没留下」时的兜底判断（看最像哪类生境），不参与正常返回。
    """
    pool: list = []
    raw_items = data.get("items") if isinstance(data, dict) else None
    if isinstance(raw_items, list):
        for it in raw_items:
            if isinstance(it, dict) and isinstance(it.get("candidates"), list):
                pool.extend(it["candidates"])
    if not pool and isinstance(data, dict) and isinstance(data.get("candidates"), list):
        pool = list(data["candidates"])
    return pool


def _normalize_guess_items(data: dict, name_index: dict, coastal: bool = True) -> list[dict]:
    """把 AI 输出整理成 items：每项 = 图里一个不同的物种，各自带自己的候选列表。

    候选的整理完全复用 _normalize_guess()（图鉴回填 / 地理过滤 / 泛称黑名单），
    这里只负责按「物品」切分。某一项的候选被地理过滤光了就整项丢掉。

    模型偶尔仍按老结构（候选平铺在顶层 candidates）返回，这里兼容成单物品，
    避免一次结构抖动就让整张照片识别失败。
    """
    raw_items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(raw_items, list) or not raw_items:
        legacy = _normalize_guess(data, name_index, coastal)
        if not legacy:
            return []
        return [{"index": 1, "label": "", "candidates": legacy}]

    items = []
    for raw in raw_items[:3]:
        if not isinstance(raw, dict):
            continue
        cands = _normalize_guess({"candidates": raw.get("candidates")}, name_index, coastal)
        if not cands:
            continue
        items.append(
            {
                "index": len(items) + 1,
                "label": str(raw.get("label") or "").strip()[:24] or f"发现 {len(items) + 1}",
                "count": _safe_count(raw.get("count")),
                "candidates": cands,
            }
        )
    return items


async def species_guess(
    image_bytes: bytes,
    content_type: str,
    species: list[dict],
    spot_name: str,
    tide_height: float | None = None,
    tide_trend: str | None = None,
    coastal: bool = True,
) -> dict:
    """AI 物种判断：优先比对图鉴，图鉴里没有再用 AI 自己的判断。失败抛 50003。"""
    disclaimer = "这是 AI 的判断，不是物种鉴定。请勿采集、勿伤害生物。"
    if not settings.dashscope_api_key:
        raise AiUnavailableError("小螃蟹这会儿有点忙，请稍后再试")

    # 名称 -> id 反查表（图鉴名 + 别名），用于把 AI 给的名称对回图鉴
    name_index: dict[str, str] = {}
    for s in species:
        name_index[s["name"]] = s["id"]
        for aka in (s.get("aka") or []):
            name_index.setdefault(str(aka), s["id"])

    # 名录带上简短特征：海洋生物靠这个对照能认得更准
    def _species_line(s: dict) -> str:
        hint = str(s.get("hint") or "").strip()[:40]
        return f"- {s['name']}" + (f"（{hint}）" if hint else "")

    species_lines = "\n".join(_species_line(s) for s in species)

    tide_text = ""
    if tide_height is not None:
        tide_text += f"潮高约 {tide_height} 米"
    if tide_trend:
        tide_text += f"，{tide_trend}"

    prompt = (
        f"这是一张用户拍的照片，拍摄地点：{spot_name}。{tide_text}\n\n"
        f"**第一步：只判断照片里的「主体」是不是潮间带/海洋生物**"
        f"（贝类、螺、蟹、虾、鱼、海星、海葵、海藻、水母这类）。\n"
        f"- **判断依据只有「主体长什么样」，与这张照片是怎么来的无关。**"
        f"即使照片是**翻拍电脑或手机屏幕**、翻拍书本杂志、拍的标本或图鉴图片，"
        f"或者画面模糊、有反光、有屏幕摩尔纹，只要**主体本身**是海洋生物，"
        f"就**必须**把 isSeaCreature 设为 true 并正常给候选。"
        f"**绝对不要因为「这像是屏幕 / 截图 / 室内」就判成不是生物。**\n"
        f"- 只有当**主体本身**确实不是海洋生物时（例如日用品、电器、鼠标、键盘、食物、"
        f"人、宠物、纯风景、画面里根本没有任何生物），才把 isSeaCreature 设为 false，"
        f"**items 必须返回空数组 []**，不要硬凑任何物种名；"
        f"同时给出 objectName：**你认为照片里到底是什么东西的常用中文名**"
        f"（例如「鼠标」「键盘」「水杯」「一本书」「猫」）；实在认不出就填「看不清的物品」；\n"
        f"- **输出前自检**：如果你已经能说出这是什么海洋生物（蟹、螺、贝、鱼…），"
        f"那 isSeaCreature 就**必须**是 true——不要一边认出是螃蟹、一边又说「不是生物」。\n\n"
        f"第二步（仅当 isSeaCreature 为 true）：**先数一数照片里有几个不同的物种**，"
        f"每个物种单独作为一项回答。\n\n"
        f"下面是本小程序图鉴的物种名录，**作为对照参考**（海洋生物靠它对照会认得更准）：\n"
        f"{species_lines}\n\n"
        f"**怎么分项（很重要）**：\n"
        f"- 一个物种 = 一项。**同一个物种的多个个体**（例如 3 只一样的沙蟹）"
        f"**合并成一项**，在 label 里写出数量，不要拆成三项；\n"
        f"- **不同物种**（例如一只螃蟹 + 一个贝壳）**各占一项**，分别给结论；\n"
        f"- 背景、礁石、海水、沙滩、桶、手等**环境物体不算**，不要为它们建项；\n"
        f"- 最多 3 项。超过 3 个不同物种时，只保留画面里最清楚、占比最大的 3 个。\n\n"
        f"**每项都要给 label**：不超过 16 个字说清它在画面里的位置或样子"
        f"（例如「左下角礁石上的螃蟹」「中间那只海星」），好让用户在照片里对上号。\n\n"
        f"**每项的候选（candidates）**：给 1 到 3 个，按可能性从高到低排列；"
        f"**很有把握时给 1 个就行**，不要硬凑；"
        f"**你觉得特征明显不符的，直接不要列出来**——宁可只给一个把握大的，"
        f"也不要用凑数的候选。\n\n"
        f"**使用方式（很重要）**：\n"
        f"- 如果生物**确实符合**名录中某一项的外观特征 → 用名录里的名称，"
        f"并在 speciesId 填上对应的 id；\n"
        f"- 如果**都不符合** → 按你自己看到的真实特征给出物种名，"
        f"**可以是名录之外的物种**（例如淡水鱼、陆生生物），speciesId 填空字符串；\n"
        f"- **绝对不要为了让答案落进名录而勉强套用**。比如照片明显是一条鲫鱼，"
        f"就答「鲫鱼」，不要因为名录里有长相相近的鱼就改成那个名字。\n\n"
        f"**【硬性要求】只许给具体物种名**：\n"
        f"- 必须能对应到一个具体物种（如「鲫鱼」「许氏平鲉」「沙蟹」）；\n"
        f"- **禁止使用泛称 / 集合名词**，例如「潮池小鱼」「小螃蟹」「小鱼」「小蟹」「小虾」"
        f"「海藻」「贝壳」「某种鱼」「蟹类」「贝类」「不认识的鱼」等，一律不许出现；\n"
        f"- 如果确实只能看出大类、判断不到具体物种，该候选的 "
        f'name 固定填「认不出具体种类」，probability 填 0，在 reason 里说明你看到的外观特征。\n'
        f"每个候选的字段要求：\n"
        f"1. name：**只写中文常用名**（如「鲫鱼」「海星」），"
        f"不要把学名、括号、别名写进 name；学名单独放在 latinName（没有就留空）；\n"
        f"2. probability：0~1 的小数，表示你认为它是该物种的概率"
        f"（同一项内各候选之和不必等于 1）；\n"
        f"3. reason：一句 20 字以内，说明依据的特征；\n"
        f"4. speciesId：符合名录的填对应 id，名录外的填空字符串，**不要编造 id**；\n"
        f"5. habitat：标注**该物种本身**属于哪一类："
        f'"sea"（海洋 / 潮间带生物）、"fresh"（淡水生物，如鲫鱼、鲤鱼、草鱼）、'
        f'"land"（陆生生物或人造物）。这个标注只描述物种本身，与拍摄地点无关。\n\n'
        f"最后给 observeTip（观察建议）和 safetyTip（安全提醒）各一句。"
        f"若 isSeaCreature 为 false，observeTip 就提示用户把镜头对准海边的小生物重拍。\n\n"
        f"只输出 JSON："
        f'{{"isSeaCreature":true,"objectName":"",'
        f'"items":[{{"label":"左下角礁石上的螃蟹",'
        f'"candidates":[{{"name":"中文常用名","latinName":"","probability":0.7,"reason":"...",'
        f'"speciesId":"符合名录填 id，否则空","habitat":"sea"}}]}}],'
        f'"observeTip":"...","safetyTip":"..."}}'
    )

    # 压缩大图：拍照原图分辨率太高会超 qwen-vl 的 max_pixels，导致识别失败
    image_bytes = _compress_for_vl(image_bytes)
    content_type = "image/jpeg"

    def _messages(prompt_text: str) -> list:
        return [
            {
                "role": "system",
                "content": "你是追潮记小程序的海洋科普助手，只输出 JSON，不要输出任何多余文字。",
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt_text},
                    {"type": "image_url", "image_url": {"url": _image_data_url(image_bytes, content_type)}},
                ],
            },
        ]

    async def _ask(prompt_text: str) -> dict:
        raw = await _chat(
            _messages(prompt_text),
            model=settings.qwen_vl_model,
            temperature=0,  # 降到 0：同一张照片尽量给出稳定结果
            # 多物品返回体明显变长，给足 token 和超时（前端 aiApi 是 25s 超时，留余量）
            max_tokens=1200,
            timeout=20,
        )
        return _parse_json(raw)

    try:
        data = await _ask(prompt)
    except Exception:
        raise AiUnavailableError("小螃蟹这会儿有点忙，请稍后再试")

    # 自相矛盾的兜底：模型一边说出「螃蟹 / 海螺」这类海洋生物的名字，一边又把
    # isSeaCreature 判成 false（多半是把翻拍屏幕、书本、标本误判成「非生物场景」）。
    # 带着纠正提示再审一次，仍不行才按非生物处理——不然就会出现「认出是螃蟹，
    # 却告诉用户这不是赶海生物」这种自打嘴巴的结果。
    obj_guess = str(data.get("objectName") or "").strip()[:20]
    if data.get("isSeaCreature") is False and _looks_like_marine(obj_guess):
        try:
            retry = await _ask(
                prompt
                + f"\n\n【补充纠正】你上一轮把主体认成了「{obj_guess}」，"
                + f"但「{obj_guess}」是海洋生物。请重新判断：isSeaCreature 必须为 true，"
                + "并按 items 给出它的候选物种。"
            )
            if retry.get("isSeaCreature") is not False and retry.get("items"):
                data = retry
        except Exception:
            pass  # 再审失败就沿用第一次的结果，不阻断主流程

    # 不是潮间带/海洋生物：先说明这是什么东西，再说明不是赶海生物
    is_sea = data.get("isSeaCreature")
    if is_sea is False:
        obj = str(data.get("objectName") or "").strip()[:20] or "看不清的物品"
        return {
            "isSeaCreature": False,
            "objectName": obj,
            "message": f"这看起来是「{obj}」，不是常见的赶海生物。",
            "items": [],
            "observeTip": str(
                data.get("observeTip")
                or "把镜头对准海边礁石、潮池、沙滩上的小生物，靠近一点再拍一张试试。"
            ),
            "safetyTip": str(
                data.get("safetyTip") or "在礁石上拍照时站稳，涨潮前离开水边。"
            ),
            "disclaimer": disclaimer,
        }

    pool = _flatten_raw_candidates(data)
    if not pool:
        # 是海洋生物却没拿到任何候选（模型返回异常 / JSON 解析失败）→ 让用户重拍
        raise AiUnavailableError("小螃蟹没看清，请靠近一点再拍一张试试")

    items = _normalize_guess_items(data, name_index, coastal)
    if not items:
        # 候选全被地理限制过滤掉了：
        # 例如人在海边，AI 认出的却都是淡水鱼 / 陆生生物
        top_habitat = ""
        for c in pool:
            if isinstance(c, dict):
                h = str(c.get("habitat") or "").strip().lower()
                if h in ("sea", "fresh", "land"):
                    top_habitat = h
                    break
        if coastal and top_habitat == "fresh":
            message = "这看起来是淡水里的鱼，不是海边能见到的赶海生物。"
        elif coastal and top_habitat == "land":
            message = "这不是海边能见到的赶海生物。"
        elif not coastal and top_habitat == "sea":
            message = "这看起来是海洋生物，但你当前不在海边附近。"
        else:
            message = "这不是常见的赶海生物。"
        return {
            "isSeaCreature": False,
            "objectName": "",
            "message": message,
            "items": [],
            "observeTip": "把镜头对准海边的礁石、潮池或沙滩上的生物，靠近一点再拍一张试试。",
            "safetyTip": "在礁石上拍照时站稳，涨潮前离开水边。",
            "disclaimer": disclaimer,
        }

    return {
        "isSeaCreature": True,
        "objectName": "",
        "message": "",
        "items": items,
        "observeTip": str(data.get("observeTip") or "下次可以再靠近一点，把生物和旁边的石头一起拍进画面。"),
        "safetyTip": str(data.get("safetyTip") or "礁石湿滑，拍照时站稳；不要把生物抠下来。"),
        "disclaimer": disclaimer,
    }


_GUESS_TEMPLATE = {
    "title": "今日观潮",
    "observations": [
        "拍到了潮间带的小发现，去图鉴里对照一下吧。",
        "今天和大人一起在海边观察，安全第一。",
        "下次退潮再来，说不定能看到更多小生物。",
    ],
    "safetyTip": "请由大人陪同，注意礁石湿滑，涨潮前离开水边。",
}


async def generate_card(
    image_bytes: bytes,
    content_type: str,
    spot_name: str,
    tide_line: str,
    guess_names: list[str],
    user_note: str | None,
) -> dict:
    """AI 生成图鉴卡（title + 3 句观察 + 1 句安全提醒）。失败降级为模板卡。"""
    if not settings.dashscope_api_key:
        return _GUESS_TEMPLATE
    guess_text = "、".join(guess_names[:3]) if guess_names else "（未做猜测）"
    prompt = (
        f"为「观潮」小程序生成一张儿童观潮图鉴卡。\n"
        f"地点：{spot_name}；{tide_line}；可能的物种：{guess_text}。\n"
        f"用户补充的观察记录：{user_note or '（无）'}。\n"
        f"请结合照片里实际看到的内容和用户的观察记录来写，要求：\n"
        f"1. title 要具体贴切：根据照片里的生物或场景起名（例如「桶里的三只小螃蟹」），"
        f"不超过 12 个字，童趣，不出现拉丁学名，不要用「今日观潮」这类笼统的标题；\n"
        f"2. observations 恰好 3 句，每句不超过 28 字，围绕照片里看到的生物和观察记录展开；\n"
        f"3. safetyTip 给 1 句安全提醒。\n"
        f"只输出 JSON："
        f'{{"title":"...","observations":["...","...","..."],"safetyTip":"..."}}'
    )

    image_bytes = _compress_for_vl(image_bytes)
    content_type = "image/jpeg"

    try:
        raw = await _chat(
            [
                {
                    "role": "system",
                    "content": "你是追潮记小程序的海洋科普助手，只输出 JSON，不要输出任何多余文字。",
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": _image_data_url(image_bytes, content_type)}},
                    ],
                },
            ],
            model=settings.qwen_vl_model,
            temperature=0.7,
            max_tokens=400,
        )
        data = _parse_json(raw)
        title = str(data.get("title") or "").strip()[:12]
        obs = data.get("observations")
        if not isinstance(obs, list) or len(obs) < 3:
            return _GUESS_TEMPLATE
        obs = [str(o).strip()[:28] for o in obs[:3]]
        safety = str(data.get("safetyTip") or _GUESS_TEMPLATE["safetyTip"]).strip()
        if not title or any(not o for o in obs):
            return _GUESS_TEMPLATE
        return {"title": title, "observations": obs, "safetyTip": safety}
    except Exception:
        return _GUESS_TEMPLATE


_IMAGE_SYNTHESIS_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis"
_IMAGE_TASK_URL = "https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"


async def _create_image_task(prompt: str) -> str | None:
    """创建通义万相文字生图异步任务，返回 task_id。"""
    headers = {
        "Authorization": f"Bearer {settings.dashscope_api_key}",
        "Content-Type": "application/json",
        "X-DashScope-Async": "enable",
    }
    payload = {
        "model": settings.image_gen_model,
        "input": {"prompt": prompt},
        "parameters": {"size": "1024*1024", "n": 1},
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(_IMAGE_SYNTHESIS_URL, json=payload, headers=headers)
    resp.raise_for_status()
    data = resp.json()
    return (data.get("output") or {}).get("task_id")


async def _poll_image_task(task_id: str, timeout: float = 90.0) -> str | None:
    """轮询生图任务直到成功，返回结果图片 URL；失败/超时返回 None。"""
    headers = {"Authorization": f"Bearer {settings.dashscope_api_key}"}
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(_IMAGE_TASK_URL.format(task_id=task_id), headers=headers)
        resp.raise_for_status()
        data = resp.json()
        output = data.get("output") or {}
        status = output.get("task_status")
        if status == "SUCCEEDED":
            results = output.get("results") or []
            if results and results[0].get("url"):
                return results[0]["url"]
            return None
        if status in ("FAILED", "CANCELED"):
            return None
        await asyncio.sleep(3)
    return None


async def generate_image(prompt: str) -> bytes | None:
    """文字生成卡通插画（通义万相 wanx），返回图片字节；失败返回 None。"""
    if not settings.dashscope_api_key:
        return None
    try:
        task_id = await _create_image_task(prompt)
        if not task_id:
            return None
        url = await _poll_image_task(task_id)
        if not url:
            return None
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(url)
        resp.raise_for_status()
        return resp.content
    except Exception:
        return None


async def _describe_image(image_bytes: bytes) -> str | None:
    """用视觉模型提取照片里的主要内容，供文生图生成关联的卡通封面。"""
    if not settings.dashscope_api_key:
        return None
    compressed = _compress_for_vl(image_bytes)
    prompt = (
        "请用一句话描述这张照片里最主要的物体和场景，重点是："
        "里面有什么生物（种类、数量）、它们放在什么容器里或在什么环境里。"
        "只输出这句话本身，不要任何解释。"
    )
    try:
        raw = await _chat(
            [
                {"role": "system", "content": "你是图片内容描述助手，只输出一句简洁的中文描述。"},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": _image_data_url(compressed, "image/jpeg")}},
                    ],
                },
            ],
            model=settings.qwen_vl_model,
            temperature=0.3,
            max_tokens=120,
        )
    except Exception:
        return None
    desc = raw.strip()
    return desc or None


async def generate_card_cover(image_bytes: bytes) -> bytes | None:
    """根据用户照片内容生成卡通封面（内容关联原图 + 卡通简约明亮风格）。

    先让视觉模型描述照片内容，再用文生图按描述生成卡通插画，
    而不是在原图上直接改画风。
    """
    desc = await _describe_image(image_bytes)
    if not desc:
        return None
    prompt = (
        f"儿童绘本卡通插画，简约明亮的扁平风格，圆润可爱的线条，明快温暖的配色，"
        f"干净简洁的背景。画面内容：{desc}"
    )
    return await generate_image(prompt)


_IMAGE2IMAGE_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/image2image/image-synthesis"


def _compress_image(image_bytes: bytes) -> bytes:
    """把图缩放到图生图接口允许的 [512, 4096] 像素范围，并转 JPEG 控制体积。

    通义万相图生图要求输入图片宽高都在 512～4096 像素之间、文件 ≤ 10MB；
    过大缩到最长边 4096，过小（如缩略图）放大到短边 512，避免 InvalidParameter。
    """
    import io

    from PIL import Image

    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    w, h = img.size

    if max(w, h) > 4096:
        scale = 4096 / max(w, h)
        img = img.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
        w, h = img.size
    if min(w, h) < 512:
        scale = 512 / min(w, h)
        img = img.resize((round(w * scale), round(h * scale)), Image.LANCZOS)

    quality = 90
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    data = buf.getvalue()
    while len(data) > 8 * 1024 * 1024 and quality > 40:
        quality -= 10
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        data = buf.getvalue()
    return data


async def stylize_image(image_bytes: bytes) -> bytes | None:
    """图生图：把用户照片转成卡通插画风格，返回图片字节；失败返回 None。"""
    if not settings.dashscope_api_key:
        return None
    try:
        compressed = _compress_image(image_bytes)
        headers = {
            "Authorization": f"Bearer {settings.dashscope_api_key}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable",
        }
        payload = {
            "model": settings.image_edit_model,
            "input": {
                "function": "stylization_all",
                "prompt": "转换成儿童绘本卡通插画风格，圆润可爱、色彩明亮温暖",
                "base_image_url": f"data:image/jpeg;base64,{base64.b64encode(compressed).decode()}",
            },
            "parameters": {"n": 1},
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(_IMAGE2IMAGE_URL, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        task_id = (data.get("output") or {}).get("task_id")
        if not task_id:
            return None
        url = await _poll_image_task(task_id)
        if not url:
            return None
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(url)
        resp.raise_for_status()
        return resp.content
    except Exception:
        return None


# ===================== Guess.candidates 的读取兼容 =====================
# 这个 JSON 列里同时躺着两种历史形状：
#   新：items 列表，每项 {index,label,count,candidates:[...]}（每项 = 图里一个东西）
#   旧：扁平的候选列表 [{rank,name,...},...]（整张图只有一个东西，给多个猜测）
# 判别是确定性的——新结构每项必有 candidates 数组，旧结构每个候选都没有。
# 接口层返回、生成图鉴卡两条链路共用，避免结构判断散落各处。


def stored_items(stored: list | None) -> list[dict]:
    """把 Guess.candidates 里存的东西统一成 items 形状（读回老记录时包成单物品）。"""
    rows = [c for c in (stored or []) if isinstance(c, dict)]
    if not rows:
        return []
    if any(isinstance(c.get("candidates"), list) for c in rows):
        return rows  # 已经是新结构
    return [
        {
            "index": 1,
            "label": "照片里的生物",
            "count": 1,
            "candidates": rows,
        }
    ]


def primary_candidates(stored: list | None) -> list[dict]:
    """取出「每个物品的代表候选」：新结构取每项 rank=1 的那个，旧结构原样返回。

    给生成图鉴卡用——它要的是「这张照片里可能有哪些物种」，
    新结构下天然得到「最多 3 种不同生物」，比旧的「同一生物的 3 个猜测」更贴切。
    """
    rows = [c for c in (stored or []) if isinstance(c, dict)]
    if not rows or not any(isinstance(c.get("candidates"), list) for c in rows):
        return rows
    out = []
    for it in stored_items(stored):
        cands = [c for c in (it.get("candidates") or []) if isinstance(c, dict)]
        if cands:
            out.append(cands[0])
    return out


# ===================== 垃圾识别 =====================

# 国标四分类：可回收物 / 有害垃圾 / 厨余垃圾 / 其他垃圾
TRASH_CATEGORIES = {
    "recyclable": "可回收物",
    "hazardous": "有害垃圾",
    "kitchen": "厨余垃圾",
    "other": "其他垃圾",
}

# 各类垃圾常见的处理提示（AI 没给 tip 时兜底）
_TRASH_TIPS = {
    "recyclable": "清空倒净、压扁后投「可回收物」桶。",
    "hazardous": "单独收好，投「有害垃圾」桶，不要随手扔在沙滩上。",
    "kitchen": "投「厨余垃圾」桶，别留在海边吸引海鸟。",
    "other": "投「其他垃圾」桶。海边捡到就带走，别留给下一场浪。",
}


def _category_label(cat: str) -> str:
    return TRASH_CATEGORIES.get((cat or "").strip().lower(), "其他垃圾")


def _safe_count(v) -> int:
    """把模型给的 count 收敛到 1~20；缺失或非法一律当 1。"""
    try:
        n = int(v)
    except (TypeError, ValueError):
        return 1
    return max(1, min(20, n))


def _normalize_trash_items(data: dict) -> list[dict]:
    """把 AI 输出整理成 items：每项 = 图里一件不同的垃圾，**每件只有一个结论**。

    垃圾识别准确率高，所以每件不给备选，也没有 rank；tip / hazardNote 从响应级
    下沉到每一件（响应级的同名旧字段只作为老结构的兜底读取）。

    模型偶尔仍按老结构（候选平铺在顶层 candidates）返回，这里兼容成多件，
    避免一次结构抖动就让整张照片识别失败。
    """
    raw_items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(raw_items, list) or not raw_items:
        raw_items = data.get("candidates") if isinstance(data, dict) else None  # 老结构兜底
    if not isinstance(raw_items, list):
        raw_items = []

    out = []
    for c in raw_items[:3]:
        if not isinstance(c, dict):
            continue
        name = str(c.get("name") or "").strip()
        if not name:
            continue
        cat = str(c.get("category") or "").strip().lower()
        if cat not in TRASH_CATEGORIES:
            cat = "other"
        try:
            prob = float(c.get("probability", c.get("confidence")) or 0)
        except (TypeError, ValueError):
            prob = 0.0
        prob = max(0.0, min(1.0, prob))
        # 先取本件的，取不到再退回响应级（兼容老结构）
        tip = str(c.get("tip") or "").strip() or str(data.get("tip") or "").strip()
        hazard = str(c.get("hazardNote") or "").strip() or str(data.get("hazardNote") or "").strip()
        out.append(
            {
                "name": name,
                "label": str(c.get("label") or "").strip()[:24],
                "count": _safe_count(c.get("count")),
                "category": cat,
                "categoryLabel": _category_label(cat),
                "probability": round(prob, 2),
                "probabilityText": _probability_text(prob),
                "reason": str(c.get("reason") or "")[:40] or "依据外观特征判断。",
                "tip": tip or _TRASH_TIPS.get(cat, _TRASH_TIPS["other"]),
                "hazardNote": hazard,
            }
        )
    # 把握大的排前面（没有 rank：每件就是一个结论）
    out.sort(key=lambda x: x["probability"], reverse=True)
    return [{"index": i + 1, **c} for i, c in enumerate(out)]


async def trash_guess(
    image_bytes: bytes,
    content_type: str,
    spot_name: str = "海边",
) -> dict:
    """AI 垃圾识别：识出是什么垃圾 + 属于哪一类（国标四分类）。失败抛 50003。"""
    disclaimer = "这是 AI 的判断，分类仅供参考，请以当地垃圾分类规定为准。"
    if not settings.dashscope_api_key:
        raise AiUnavailableError("小螃蟹这会儿有点忙，请稍后再试")

    prompt = (
        f"这是用户在海边（{spot_name}）拍到的一张照片。\n\n"
        f"**第一步：通读整张照片，数清楚画面里有「几件不同的垃圾」**"
        f"（塑料袋、瓶子、渔网、烟头、泡沫、纸屑、金属件等人造废弃物）。\n"
        f"- **同一件垃圾只报一次**：不要把瓶盖和瓶身、绳子和网片拆成两件；\n"
        f"- **同一类多件相同的垃圾算一件**（沙滩上 3 个一样的塑料瓶 = 一件），"
        f"把这一件的 count 填成件数（如 3）；件数看不清就填 1；\n"
        f"- **不同种类必须分成不同条目**，同一个名字不许出现在两条里；\n"
        f"- **不要把自然物或生物算成垃圾**：礁石、贝壳、海藻、枯木、水母、鸟、"
        f"脚印、影子、浪花都不算；\n"
        f"- 看不清的小碎屑不要报，**宁少不滥**；最多报 3 件；\n"
        f"- 如果照片里没有垃圾（是生物、风景、人物、干净的礁石沙滩），"
        f"把 isTrash 设为 false，**items 必须返回空数组 []**。\n\n"
        f"第二步（仅当 isTrash 为 true）：对**每一件垃圾**判断属于哪一类。\n"
        f"分类标准（四分类，category 只能填下面的英文值）：\n"
        f"- recyclable（可回收物）：塑料瓶、玻璃瓶、易拉罐、金属、纸箱、硬质塑料\n"
        f"- hazardous（有害垃圾）：电池、药品、灯管、油漆桶、化学品容器\n"
        f"- kitchen（厨余垃圾）：食物残渣、果皮、海鲜壳（海边少见）\n"
        f"- other（其他垃圾）：烟头、渔网渔线、泡沫塑料、一次性餐具、口罩、"
        f"无法回收的杂物\n\n"
        f"对**每一件垃圾**给：\n"
        f"1. label：不超过 16 个字说清它在画面里的位置或样子"
        f"（例如「画面中央沙滩上的塑料瓶」），好让用户在照片里对上号；\n"
        f"2. count：这一件在画面里有几个（默认 1）；\n"
        f"3. name：**具体物品名**（如「塑料矿泉水瓶」「废弃渔网」），不要只写「垃圾」；\n"
        f"4. category：上面四个英文值之一；\n"
        f"5. probability：0~1 的小数，表示你有多确定；\n"
        f"6. reason：20 字以内的判断依据；\n"
        f"7. tip：**这一件**该怎么处理，一句话；\n"
        f"8. hazardNote：**这一件**对海洋生物或环境有什么危害，一句话"
        f"（没有明显危害就填空字符串）。\n"
        f"**每件只给一个结论，不要给备选答案。**\n\n"
        f"只输出 JSON："
        f'{{"isTrash":true,'
        f'"items":[{{"label":"画面中央沙滩上的塑料瓶","count":1,'
        f'"name":"塑料矿泉水瓶","category":"recyclable","probability":0.9,'
        f'"reason":"透明塑料瓶带蓝色标签","tip":"清空压扁后投可回收物桶",'
        f'"hazardNote":"易被海洋生物误食"}}]}}'
    )

    image_bytes = _compress_for_vl(image_bytes)
    content_type = "image/jpeg"

    try:
        raw = await _chat(
            [
                {
                    "role": "system",
                    "content": "你是追潮记小程序的海洋环保助手，只输出 JSON，不要输出任何多余文字。",
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": _image_data_url(image_bytes, content_type)}},
                    ],
                },
            ],
            model=settings.qwen_vl_model,
            temperature=0,
            max_tokens=1000,
            timeout=20,
        )
    except Exception:
        raise AiUnavailableError("小螃蟹这会儿有点忙，请稍后再试")

    data = _parse_json(raw)

    if data.get("isTrash") is False:
        return {
            "isTrash": False,
            "message": "照片里没看到垃圾，干净的海边真好。",
            "items": [],
            "disclaimer": disclaimer,
        }

    items = _normalize_trash_items(data)
    if not items:
        raise AiUnavailableError("小螃蟹没看清，请靠近一点再拍一张试试")

    return {
        "isTrash": True,
        "message": "",
        "items": items,
        "disclaimer": disclaimer,
    }

"""内容安全（首期轻量实现）。

真实内容安全待接入云厂商接口（对应错误码 50004）。当前先拦截最明显的问题：
- 联系方式（手机号 / 微信号 / QQ 号），覆盖评论「禁止联系方式」规则；
- 明显违规词。

图片暂不做检测（返回通过），后续接入图片审核。
"""
import re

from app.core.exceptions import ContentSafetyError

_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_WECHAT_RE = re.compile(r"(微信|v信|vx|weixin|加我|加v)[:：\s]*[a-zA-Z][a-zA-Z0-9_-]{4,}", re.IGNORECASE)
_QQ_RE = re.compile(r"(qq|扣扣)[:：\s]*\d{5,}", re.IGNORECASE)
_BANNED_WORDS = ("赌博", "博彩", "色情", "代考", "刷单", "套现")


def check_text(text: str | None) -> bool:
    """返回 True 表示通过；False 表示命中规则。"""
    if not text:
        return True
    if _PHONE_RE.search(text):
        return False
    if _WECHAT_RE.search(text):
        return False
    if _QQ_RE.search(text):
        return False
    for w in _BANNED_WORDS:
        if w in text:
            return False
    return True


def assert_text_safe(*texts: str | None) -> None:
    """任一文本命中规则即抛 50004。"""
    for t in texts:
        if not check_text(t):
            raise ContentSafetyError("内容未通过安全检测")

"""赶海点位的关键词搜索：匹配哪些字段、按什么顺序排。

`GET /spots?keyword=` 和 `GET /search`（首页搜索）的点位部分共用这一套，
免得两处各写一份、搜出来的东西还不一样。

为什么把 `district` 也算命中
--------------------------
点位名都带城市前缀（「青岛·石老人」），但区名不在名字里。用户很自然会搜
「黄岛」「崂山」「即墨」—— 之前只匹配 name/city/提示/描述，这几个词一个都搜不到
（黄岛区 14 个点位，搜「黄岛」返回 0）。加上 district 之后这类搜索才有结果。

为什么按命中位置排
------------------
原来固定按热度排，搜「赶海」时名字里带「赶海」的和描述里提一句的混在一起。
现在名字命中的排最前，其次区、市，最后才是正文里提一嘴的。
注意点位名带「青岛·」前缀，所以没有做「前缀命中」这一档 —— 永远命中不了。
"""
from __future__ import annotations

from sqlalchemy import case, or_

from app.db.models import Spot


def spot_keyword_where(kw: str):
    """关键词的匹配条件：名称 / 所属区 / 城市 / 观察提示 / 描述，任一命中即算。

    autoescape=True：用户输的 % 和 _ 当普通字符，不当 SQL 通配符。
    """
    return or_(
        Spot.name.contains(kw, autoescape=True),
        Spot.district.contains(kw, autoescape=True),
        Spot.city.contains(kw, autoescape=True),
        Spot.observe_hint.contains(kw, autoescape=True),
        Spot.description.contains(kw, autoescape=True),
    )


def spot_keyword_rank(kw: str):
    """命中位置 -> 排序权重（越小越靠前）。

    0 名称命中 > 1 区命中 > 2 城市命中 > 3 只在提示/描述里提到。
    """
    return case(
        (Spot.name.contains(kw, autoescape=True), 0),
        (Spot.district.contains(kw, autoescape=True), 1),
        (Spot.city.contains(kw, autoescape=True), 2),
        else_=3,
    )


def spot_keyword_order(kw: str) -> list:
    """带关键词时的排序：先按命中位置，同档内热度高的靠前，最后按 id 稳定。"""
    return [spot_keyword_rank(kw), Spot.heat.desc(), Spot.id]

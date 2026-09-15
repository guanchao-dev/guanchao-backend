from typing import Any

from app.core.utils import new_request_id


def ok(data: Any = None, message: str = "ok") -> dict:
    """成功响应统一封装。"""
    return {"code": 0, "message": message, "data": data, "requestId": new_request_id()}


def paginated(items: list, page: int, page_size: int, total: int) -> dict:
    """分页 data 结构。"""
    return {"list": items, "page": page, "pageSize": page_size, "total": total}

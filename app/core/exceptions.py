from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.utils import new_request_id


class ApiError(Exception):
    """业务异常基类。code / http_status 对应接口文档 §1.5。"""

    code = 50001
    message = "服务器内部错误"
    http_status = 500

    def __init__(self, message: str | None = None):
        if message is not None:
            self.message = message
        super().__init__(self.message)


class BadRequestError(ApiError):
    code = 40001
    message = "参数错误"
    http_status = 400


class InvalidFileError(ApiError):
    code = 40002
    message = "文件类型或大小不合法"
    http_status = 400


class UnauthorizedError(ApiError):
    code = 40101
    message = "未登录或 token 失效"
    http_status = 401


class GuardianConsentError(ApiError):
    code = 40301
    message = "未完成监护人同意"
    http_status = 403


class ForbiddenError(ApiError):
    code = 40302
    message = "无权访问该资源"
    http_status = 403


class NotFoundError(ApiError):
    code = 40401
    message = "资源不存在"
    http_status = 404


class ConflictError(ApiError):
    code = 40901
    message = "重复提交"
    http_status = 409


class RateLimitError(ApiError):
    code = 42901
    message = "触发限流"
    http_status = 429


class InternalError(ApiError):
    code = 50001
    message = "服务器内部错误"
    http_status = 500


class ThirdPartyTimeoutError(ApiError):
    code = 50002
    message = "第三方潮汐/天气超时，已降级"
    http_status = 503


class AiUnavailableError(ApiError):
    code = 50003
    message = "AI 服务不可用"
    http_status = 503


class ContentSafetyError(ApiError):
    code = 50004
    message = "内容安全未通过"
    http_status = 422


def _error_response(code: int, message: str, http_status: int) -> JSONResponse:
    return JSONResponse(
        status_code=http_status,
        content={"code": code, "message": message, "data": None, "requestId": new_request_id()},
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError):
        return _error_response(exc.code, exc.message, exc.http_status)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        return _error_response(40001, "参数错误", 400)

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception):
        return _error_response(50001, "服务器内部错误", 500)

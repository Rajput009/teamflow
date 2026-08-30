import logging

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exceptions import AppError

logger = logging.getLogger("teamflow")

# Human-readable fallbacks per status code for framework-raised HTTP errors.
_STATUS_FALLBACKS = {
    400: ("BAD_REQUEST", "Bad request."),
    401: ("NOT_AUTHENTICATED", "Not authenticated."),
    403: ("FORBIDDEN", "Forbidden."),
    404: ("NOT_FOUND", "Resource not found."),
    405: ("METHOD_NOT_ALLOWED", "Method not allowed."),
    422: ("VALIDATION_ERROR", "Request data is invalid."),
}


def _envelope(code: str, message: str, details: list | None = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details or []}}


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """Domain errors raised by services. 401s also carry the RFC 6750
    WWW-Authenticate challenge some HTTP clients depend on."""
    headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
    return JSONResponse(
        status_code=exc.status_code,
        content=_envelope(exc.code, exc.message, exc.details),
        headers=headers,
    )


async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Pydantic rejections, reshaped into field-level detail entries."""
    details = []
    for err in exc.errors():
        loc = [str(part) for part in err["loc"]]
        # drop the leading "body"/"query"/"path" container name
        field = ".".join(loc[1:]) if len(loc) > 1 else ".".join(loc)
        details.append({"field": field, "issue": err["msg"]})
    return JSONResponse(
        status_code=422,
        content=_envelope("VALIDATION_ERROR", "Request data is invalid.", details),
    )


async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """Framework-raised HTTP errors (unknown routes, wrong methods, etc.)."""
    code, message = _STATUS_FALLBACKS.get(
        exc.status_code, ("HTTP_ERROR", str(exc.detail))
    )
    return JSONResponse(status_code=exc.status_code, content=_envelope(code, message))


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Last line of defense: log everything server-side, leak nothing client-side.

    The correlation between what the client sees and what we can find in logs
    arrives in V4 with request IDs.
    """
    logger.exception(
        "Unhandled exception on %s %s", request.method, request.url.path
    )
    return JSONResponse(
        status_code=500,
        content=_envelope("INTERNAL_ERROR", "An unexpected error occurred."),
    )

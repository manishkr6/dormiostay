"""Safe, typed API errors and global exception handlers."""

import logging
from collections.abc import Mapping
from typing import Any

import sentry_sdk
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class AppError(Exception):
    """An expected error safe to expose to the client."""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        fields: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.fields = dict(fields or {})


def _body(
    request: Request,
    code: str,
    message: str,
    fields: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "fields": dict(fields or {}),
            "request_id": getattr(request.state, "request_id", "unknown"),
        }
    }


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        _body(request, exc.code, exc.message, exc.fields),
        status_code=exc.status_code,
    )


async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    if exc.status_code == 404:
        code, message = "NOT_FOUND", "We couldn't find that."
    elif exc.status_code == 405:
        code, message = "METHOD_NOT_ALLOWED", "That action is not available."
    else:
        code, message = "HTTP_ERROR", "The request could not be completed."
    return JSONResponse(_body(request, code, message), status_code=exc.status_code)


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    fields: dict[str, str] = {}
    for error in exc.errors():
        location = [str(part) for part in error.get("loc", ()) if part != "body"]
        if location:
            fields[".".join(location)] = str(error.get("msg", "Invalid value"))
    return JSONResponse(
        _body(request, "VALIDATION_ERROR", "Please check the highlighted fields.", fields),
        status_code=422,
    )


async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "unknown")
    logger.error("Unhandled application error", extra={"request_id": request_id})
    with sentry_sdk.new_scope() as scope:
        scope.set_tag("request_id", request_id)
        sentry_sdk.capture_message("Application error", level="error")
    return JSONResponse(
        _body(request, "INTERNAL_SERVER_ERROR", "Something went wrong on our side."),
        status_code=500,
    )


def register_exception_handlers(application: FastAPI) -> None:
    application.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
    application.add_exception_handler(StarletteHTTPException, http_error_handler)  # type: ignore[arg-type]
    application.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
    application.add_exception_handler(Exception, unexpected_error_handler)

"""Request correlation middleware."""

import logging
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)


class RequestIdMiddleware:
    """Attach a safe UUID request ID to state, response headers, and logs."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = self._request_id(scope)
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        with logging_context(request_id):
            try:
                await self.app(scope, receive, send_with_request_id)
            except Exception as exc:
                # ServerErrorMiddleware sits outside user middleware in Starlette
                # and otherwise cannot receive our correlation header.
                from fastapi import Request

                from app.core.errors import unexpected_error_handler

                response = await unexpected_error_handler(Request(scope, receive), exc)
                await response(scope, receive, send_with_request_id)

    @staticmethod
    def _request_id(scope: Scope) -> str:
        for name, value in scope.get("headers", []):
            if name.lower() == b"x-request-id":
                try:
                    return str(uuid.UUID(value.decode("ascii")))
                except (ValueError, UnicodeDecodeError):
                    break
        return str(uuid.uuid4())


class logging_context:
    """Small logging filter context used to attach the current request ID."""

    def __init__(self, request_id: str) -> None:
        self.request_id = request_id
        self.filter = _RequestIdFilter(request_id)
        self.handlers: list[logging.Handler] = []

    def __enter__(self) -> None:
        root = logging.getLogger()
        for handler in root.handlers:
            handler.addFilter(self.filter)
            self.handlers.append(handler)

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        for handler in self.handlers:
            handler.removeFilter(self.filter)


class _RequestIdFilter(logging.Filter):
    def __init__(self, request_id: str) -> None:
        super().__init__()
        self.request_id = request_id

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = self.request_id
        return True

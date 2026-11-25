"""FastAPI dependencies for authentication, credential transport, and security guards."""

from typing import Annotated

from fastapi import Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.db.models import User, UserSession
from app.db.session import get_session, get_session_factory
from app.services import auth_service


def require_auth_endpoints_enabled(
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """T-06 Activation Guard.

    Enforces that /api/v1/auth endpoints remain disabled in deployment until T-07 (CSRF)
    and T-08 (Rate Limiting) security protections are fully implemented and verified.
    """
    if not settings.auth_endpoints_enabled:
        raise AppError(
            code="SERVICE_UNAVAILABLE",
            message=(
                "Authentication endpoints are temporarily disabled pending security "
                "hardening (T-07 CSRF and T-08 rate limiting)."
            ),
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )


def extract_raw_session_token(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> str:
    """Extract raw session token from either Web cookie or Native Mobile Authorization header.

    Strict security invariants:
    - Rejects ambiguous requests carrying both session cookie and Authorization header (HTTP 400).
    - Validates Bearer format and never falls back to cookie on invalid/malformed bearer (HTTP 401).
    - Requires at least one valid credential source (HTTP 401).
    """
    cookie_token: str | None = request.cookies.get(settings.session_cookie_name)
    auth_header: str | None = request.headers.get("Authorization")

    # 1. Ambiguous transport check: reject requests with both credentials
    if cookie_token is not None and auth_header is not None:
        raise AppError(
            code="AMBIGUOUS_CREDENTIALS",
            message=(
                "Ambiguous authentication credentials: both session cookie "
                "and Authorization header provided."
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # 2. Authorization header transport (Native Mobile)
    if auth_header is not None:
        parts = auth_header.split(" ", 1)
        if len(parts) != 2 or parts[0].lower() != "bearer":
            raise AppError(
                code="INVALID_AUTHORIZATION_HEADER",
                message="Invalid Authorization header format. Must be 'Bearer <session_token>'.",
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
        bearer_token = parts[1].strip()
        if not bearer_token:
            raise AppError(
                code="EMPTY_BEARER_TOKEN",
                message="Bearer token cannot be empty.",
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
        return bearer_token

    # 3. Cookie transport (Web)
    if cookie_token is not None:
        stripped_cookie = cookie_token.strip()
        if not stripped_cookie:
            raise AppError(
                code="EMPTY_SESSION_COOKIE",
                message="Session cookie cannot be empty.",
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
        return stripped_cookie

    # 4. Neither credential provided
    raise AppError(
        code="UNAUTHORIZED",
        message="Authentication credentials required.",
        status_code=status.HTTP_401_UNAUTHORIZED,
    )


async def get_current_session_and_user(
    request: Request,
    raw_token: Annotated[str, Depends(extract_raw_session_token)],
    db_session: Annotated[AsyncSession, Depends(get_session)],
    session_factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> tuple[UserSession, User]:
    """Validate server-side session and establish authenticated request identity."""
    user_session, user = await auth_service.validate_session(
        session=db_session,
        raw_token=raw_token,
        settings=settings,
        session_factory=session_factory,
    )

    if user.role not in {"tenant", "owner", "admin"}:
        raise AppError(
            code="FORBIDDEN_ROLE",
            message="Invalid user role.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    # Establish transaction-local PostgreSQL RLS identity context
    from sqlalchemy import text

    await db_session.execute(
        text(
            "SELECT set_config('app.user_id', :user_id, true), set_config('app.role', :role, true)"
        ),
        {"user_id": str(user.id), "role": user.role},
    )

    # Propagate trusted identity onto request state for downstream RLS and logging
    request.state.user_id = user.id
    request.state.role = user.role

    return user_session, user


async def get_current_user(
    session_and_user: Annotated[tuple[UserSession, User], Depends(get_current_session_and_user)],
) -> User:
    """Dependency yielding the currently authenticated User model."""
    return session_and_user[1]

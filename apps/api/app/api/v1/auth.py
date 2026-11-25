"""Authentication API endpoints (T-06)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import get_current_session_and_user, require_auth_endpoints_enabled
from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.db.models import User, UserSession
from app.db.session import get_session, get_session_factory
from app.schemas.auth import (
    AuthResponseNative,
    AuthResponseWeb,
    LoginRequest,
    MeResponse,
    ProfileResponse,
    SignupRequest,
    StatusResponse,
    UserResponse,
)
from app.services import auth_service

router = APIRouter(
    dependencies=[Depends(require_auth_endpoints_enabled)],
)


@router.post(
    "/signup",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new Tenant or Owner account",
)
async def signup(
    payload: SignupRequest,
    db_session: Annotated[AsyncSession, Depends(get_session)],
) -> UserResponse:
    """Create a new account with Argon2id password hashing and profile record."""
    user = await auth_service.signup(session=db_session, payload=payload)
    return UserResponse(
        id=user.id,
        email=user.email,
        role=user.role,
        status=user.status,
        email_verified=user.email_verified_at is not None,
        created_at=user.created_at,
    )


@router.post(
    "/login",
    response_model=None,
    summary="Authenticate and establish server-side session",
)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db_session: Annotated[AsyncSession, Depends(get_session)],
    session_factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthResponseWeb | AuthResponseNative:
    """Authenticate credentials.

    - Web mode: Issues HttpOnly cookie and returns user profile without token in JSON.
    - Native mode: Issues token in JSON body once for Expo SecureStore; does not set cookie.
    """
    client_type = payload.client_type.lower()
    header_client_type = request.headers.get("X-Client-Type")
    if header_client_type is not None:
        normalized_header = header_client_type.strip().lower()
        if normalized_header not in {"web", "native"}:
            raise AppError(
                code="INVALID_CLIENT_TYPE",
                message="Invalid X-Client-Type header value. Must be 'web' or 'native'.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        if normalized_header != client_type:
            raise AppError(
                code="AMBIGUOUS_TRANSPORT",
                message="Conflicting client_type in request body and X-Client-Type header.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

    # Enforce that Native login does not carry ambient session cookies
    existing_cookie = request.cookies.get(settings.session_cookie_name)
    if client_type == "native" and existing_cookie is not None:
        raise AppError(
            code="AMBIGUOUS_CREDENTIALS",
            message="Native authentication must not include session cookies.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    is_secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"

    raw_token, user, user_session = await auth_service.login(
        session=db_session,
        payload=payload,
        settings=settings,
        session_factory=session_factory,
        client_type=client_type,
        existing_cookie_token=existing_cookie,
        is_secure_transport=is_secure,
    )

    user_data = UserResponse(
        id=user.id,
        email=user.email,
        role=user.role,
        status=user.status,
        email_verified=user.email_verified_at is not None,
        created_at=user.created_at,
    )

    if client_type == "web":
        # Web transport: HttpOnly cookie ONLY
        response.set_cookie(
            key=settings.session_cookie_name,
            value=raw_token,
            httponly=True,
            secure=settings.is_production or (settings.app_env != "development"),
            samesite="lax",
            path="/",
            max_age=settings.session_absolute_timeout_days * 86400,
        )
        return AuthResponseWeb(user=user_data)

    # Native Mobile transport: Token in non-cacheable JSON body ONLY; NO cookie
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    return AuthResponseNative(token=raw_token, user=user_data)


@router.post(
    "/logout",
    response_model=StatusResponse,
    summary="Revoke the current server-side session",
)
async def logout(
    request: Request,
    response: Response,
    session_and_user: Annotated[tuple[UserSession, User], Depends(get_current_session_and_user)],
    db_session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> StatusResponse:
    """Revoke the current session in PostgreSQL and clear cookie if present."""
    user_session, _ = session_and_user
    await auth_service.logout(session=db_session, session_id=user_session.id)
    if request.cookies.get(settings.session_cookie_name) is not None:
        response.delete_cookie(
            key=settings.session_cookie_name,
            path="/",
            httponly=True,
            secure=settings.is_production or (settings.app_env != "development"),
            samesite="lax",
        )
    return StatusResponse(status="ok")


@router.get(
    "/me",
    response_model=MeResponse,
    summary="Retrieve current authenticated user details and profile",
)
async def me(
    session_and_user: Annotated[tuple[UserSession, User], Depends(get_current_session_and_user)],
    db_session: Annotated[AsyncSession, Depends(get_session)],
) -> MeResponse:
    """Return authenticated account details without sensitive credentials or tokens."""
    _, user = session_and_user
    row = await auth_service.get_user_profile(session=db_session, user_id=user.id)

    profile_data: ProfileResponse | None = None
    if row and row[1]:
        p = row[1]
        profile_data = ProfileResponse(
            full_name=p.full_name,
            phone=p.phone,
            avatar_url=p.avatar_url,
            gender=p.gender,
            preferred_budget_min=p.preferred_budget_min,
            preferred_budget_max=p.preferred_budget_max,
            preferred_areas=p.preferred_areas or [],
            institution_id=p.institution_id,
        )

    return MeResponse(
        user=UserResponse(
            id=user.id,
            email=user.email,
            role=user.role,
            status=user.status,
            email_verified=user.email_verified_at is not None,
            created_at=user.created_at,
        ),
        profile=profile_data,
    )

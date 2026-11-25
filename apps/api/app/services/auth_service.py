"""Authentication business logic service."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import AppError
from app.core.security import (
    generate_session_token,
    hash_password,
    hash_session_token,
    validate_password_strength,
    verify_dummy_password,
    verify_password,
)
from app.db.models import User, UserProfile, UserSession
from app.db.repositories import session_repo, user_repo
from app.schemas.auth import LoginRequest, SignupRequest


async def signup(session: AsyncSession, payload: SignupRequest) -> User:
    """Register a new Tenant or Owner account.

    Rejects Admin role registrations, validates password strength, ensures case-insensitive
    email uniqueness, hashes password with Argon2id, and creates user + profile atomically.
    """
    # 1. Enforce public registration role restriction
    if payload.role not in {"tenant", "owner"}:
        raise AppError(
            code="FORBIDDEN_ROLE",
            message="Public registration is limited to tenant and owner roles only.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # 2. Validate password strength
    is_valid, err_msg = validate_password_strength(payload.password)
    if not is_valid:
        raise AppError(
            code="WEAK_PASSWORD",
            message=err_msg or "Password does not meet complexity requirements.",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    # 3. Check case-insensitive email uniqueness
    existing_user = await user_repo.get_user_by_email_ci(session, payload.email)
    if existing_user is not None:
        raise AppError(
            code="EMAIL_ALREADY_EXISTS",
            message="An account with this email address already exists.",
            status_code=status.HTTP_409_CONFLICT,
        )

    # 4. Hash password with pwdlib Argon2id
    password_hash = hash_password(payload.password)

    # 5. Create user and user profile atomically with savepoint and constraint error handling
    try:
        async with session.begin_nested():
            user = await user_repo.create_user(
                session=session,
                email=payload.email,
                password_hash=password_hash,
                role=payload.role,
                status="active",
            )

            await user_repo.create_user_profile(
                session=session,
                user_id=user.id,
                full_name=payload.full_name,
                phone=payload.phone,
                institution_id=payload.institution_id,
            )
    except IntegrityError as exc:
        orig = getattr(exc, "orig", exc)
        cause = getattr(orig, "__cause__", orig)
        constraint_name = getattr(orig, "constraint_name", None) or getattr(
            cause, "constraint_name", None
        )
        sqlstate = (
            getattr(orig, "sqlstate", None)
            or getattr(cause, "sqlstate", None)
            or getattr(orig, "pgcode", None)
        )

        # 1. Structured PostgreSQL diagnostics path (exclusive when structured attributes exist)
        if constraint_name is not None or sqlstate is not None:
            if sqlstate == "23505" and constraint_name == "uq_users_email_ci":
                raise AppError(
                    code="EMAIL_ALREADY_EXISTS",
                    message="An account with this email address already exists.",
                    status_code=status.HTTP_409_CONFLICT,
                ) from exc
            if (
                sqlstate == "23503"
                and constraint_name == "fk_user_profiles_institution_id_institutions"
            ):
                raise AppError(
                    code="INVALID_INSTITUTION",
                    message="The specified institution does not exist.",
                    status_code=status.HTTP_400_BAD_REQUEST,
                ) from exc
            raise

        # 2. Narrow fallback ONLY when BOTH constraint_name and sqlstate are None (e.g. SQLite)
        err_str = str(orig).lower()
        if "uq_users_email_ci" in err_str or "users.email" in err_str:
            raise AppError(
                code="EMAIL_ALREADY_EXISTS",
                message="An account with this email address already exists.",
                status_code=status.HTTP_409_CONFLICT,
            ) from exc
        if "fk_user_profiles_institution_id_institutions" in err_str:
            raise AppError(
                code="INVALID_INSTITUTION",
                message="The specified institution does not exist.",
                status_code=status.HTTP_400_BAD_REQUEST,
            ) from exc

        raise

    return user


async def login(
    session: AsyncSession,
    payload: LoginRequest,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    client_type: str = "web",
    existing_cookie_token: str | None = None,
    is_secure_transport: bool = True,
) -> tuple[str, User, UserSession]:
    """Authenticate user credentials and issue a server-side session.

    Enforces case-insensitive email lookup, constant-time dummy verify on nonexistent users,
    generic error messages, account suspension/ban checks, native Admin rejection,
    session rotation on web login, and returns an opaque token alongside user models.
    """
    if session_factory is None:
        raise RuntimeError("SESSION_FACTORY_REQUIRED")

    # 1. Lookup user by email (case-insensitive)
    user = await user_repo.get_user_by_email_ci(session, payload.email)
    if user is None:
        # Prevent timing attacks by executing a dummy Argon2id verification
        verify_dummy_password()
        raise AppError(
            code="AUTHENTICATION_FAILED",
            message="Email or password is incorrect.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    # 2. Verify password (fail-safe and persist failed attempt counter in independent transaction)
    if not verify_password(payload.password, user.password_hash):
        async with session_factory() as sec_session, sec_session.begin():
            await user_repo.update_login_failure(sec_session, user.id)
        raise AppError(
            code="AUTHENTICATION_FAILED",
            message="Email or password is incorrect.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    # 3. Enforce account status checks (fail-closed for any non-active status)
    if user.status != "active":
        if user.status == "suspended":
            msg = "Account is suspended."
        elif user.status == "banned":
            msg = "Account is banned."
        else:
            msg = "Account is inactive."
        raise AppError(
            code="ACCOUNT_DISABLED",
            message=msg,
            status_code=status.HTTP_403_FORBIDDEN,
        )

    # 4. Enforce client-type and role boundaries
    if user.role == "admin":
        if client_type == "native":
            raise AppError(
                code="ADMIN_NATIVE_LOGIN_DISALLOWED",
                message="Admin login is not permitted on native mobile.",
                status_code=status.HTTP_403_FORBIDDEN,
            )
        # T-06 requirement: do not create public admin login or bypass T-13 MFA
        raise AppError(
            code="ADMIN_PORTAL_REQUIRED",
            message="Admin accounts must authenticate through the dedicated admin portal.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    # 5. Require HTTPS for native mobile login outside development
    if client_type == "native" and not is_secure_transport and settings.app_env != "development":
        raise AppError(
            code="INSECURE_TRANSPORT",
            message="HTTPS is required for native authentication outside development.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    now = datetime.now(UTC)

    # 6. Session Rotation: If Web client presented an existing ambient cookie, revoke it
    if client_type == "web" and existing_cookie_token:
        old_token_hash = hash_session_token(existing_cookie_token.strip())
        old_row = await session_repo.get_session_with_user_by_token_hash(session, old_token_hash)
        if old_row and old_row[0].revoked_at is None:
            await session_repo.revoke_session(
                session=session,
                session_id=old_row[0].id,
                revoked_at=now,
                reason="session_rotation",
            )

    # 7. Generate 32-byte cryptographic token and SHA-256 hash
    raw_token = generate_session_token()
    token_hash = hash_session_token(raw_token)

    if user.role == "admin":
        idle_delta = timedelta(minutes=settings.admin_session_idle_timeout_minutes)
        abs_delta = timedelta(hours=settings.admin_session_absolute_timeout_hours)
    else:
        idle_delta = timedelta(minutes=settings.session_idle_timeout_minutes)
        abs_delta = timedelta(days=settings.session_absolute_timeout_days)

    expires_at = now + idle_delta
    absolute_expires_at = now + abs_delta

    # 8. Persist session in PostgreSQL user_sessions
    user_session = await session_repo.create_session(
        session=session,
        user_id=user.id,
        token_hash=token_hash,
        expires_at=expires_at,
        absolute_expires_at=absolute_expires_at,
        created_at=now,
    )

    # 9. Reset failed login counter and record last login
    await user_repo.update_login_success(session, user.id, now)

    return raw_token, user, user_session


async def validate_session(
    session: AsyncSession,
    raw_token: str,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[UserSession, User]:
    """Validate server-side session by token hash, enforcing idle/absolute expiry and status.

    Slides the idle expiration window up to absolute_expires_at if valid.
    """
    if session_factory is None:
        raise RuntimeError("SESSION_FACTORY_REQUIRED")

    token_hash = hash_session_token(raw_token)
    session_row = await session_repo.get_session_with_user_by_token_hash(session, token_hash)

    if session_row is None:
        raise AppError(
            code="SESSION_INVALID",
            message="Session is invalid or expired.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    user_session, user = session_row

    # 1. Check explicit revocation
    if user_session.revoked_at is not None:
        raise AppError(
            code="SESSION_REVOKED",
            message="Session has been revoked.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    now = datetime.now(UTC)

    # Ensure tz-aware comparisons
    abs_expires = (
        user_session.absolute_expires_at.replace(tzinfo=UTC)
        if user_session.absolute_expires_at.tzinfo is None
        else user_session.absolute_expires_at
    )
    idle_expires = (
        user_session.expires_at.replace(tzinfo=UTC)
        if user_session.expires_at.tzinfo is None
        else user_session.expires_at
    )

    # 2. Check absolute expiry (persist revocation in independent transaction before raising)
    if now >= abs_expires:
        async with session_factory() as sec_session, sec_session.begin():
            await session_repo.revoke_session(
                sec_session, user_session.id, now, reason="absolute_expiry"
            )
        raise AppError(
            code="SESSION_EXPIRED",
            message="Session has expired.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    # 3. Check idle expiry (persist revocation in independent transaction before raising)
    if now >= idle_expires:
        async with session_factory() as sec_session, sec_session.begin():
            await session_repo.revoke_session(
                sec_session, user_session.id, now, reason="idle_expiry"
            )
        raise AppError(
            code="SESSION_EXPIRED",
            message="Session has expired.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    # 4. Check account status (fail closed)
    if user.status != "active":
        if user.status == "suspended":
            msg = "Account is suspended."
        elif user.status == "banned":
            msg = "Account is banned."
        else:
            msg = "Account is inactive."
        raise AppError(
            code="ACCOUNT_DISABLED",
            message=msg,
            status_code=status.HTTP_403_FORBIDDEN,
        )

    # 5. Slide idle expiry window
    if user.role == "admin":
        idle_delta = timedelta(minutes=settings.admin_session_idle_timeout_minutes)
    else:
        idle_delta = timedelta(minutes=settings.session_idle_timeout_minutes)

    new_idle = now + idle_delta
    new_expires_at = min(new_idle, abs_expires)

    updated = await session_repo.update_session_activity(
        session=session,
        session_id=user_session.id,
        last_activity_at=now,
        new_expires_at=new_expires_at,
    )
    if not updated:
        raise AppError(
            code="SESSION_REVOKED",
            message="Session has been revoked.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    user_session.last_activity_at = now
    user_session.expires_at = new_expires_at

    return user_session, user


async def logout(session: AsyncSession, session_id: UUID) -> None:
    """Revoke the current server session immediately."""
    now = datetime.now(UTC)
    await session_repo.revoke_session(session, session_id, now, reason="logout")


async def revoke_all_sessions(
    session: AsyncSession,
    user_id: UUID,
    reason: str,
) -> int:
    """Revoke all active sessions for a user (e.g. password reset, suspension, ban)."""
    now = datetime.now(UTC)
    return await session_repo.revoke_all_user_sessions(session, user_id, now, reason)


async def get_user_profile(
    session: AsyncSession,
    user_id: UUID,
) -> tuple[User, UserProfile | None] | None:
    """Fetch user account and profile for /auth/me."""
    return await user_repo.get_user_with_profile(session, user_id)

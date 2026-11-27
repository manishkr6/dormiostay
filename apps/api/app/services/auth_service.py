"""Authentication business logic service."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import Request, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.security import (
    generate_action_token,
    generate_session_token,
    hash_action_token,
    hash_password,
    hash_session_token,
    validate_password_strength,
    verify_dummy_password,
    verify_password,
)
from app.db.models import User, UserProfile, UserSession
from app.db.repositories import (
    password_reset_repo,
    session_repo,
    user_repo,
    verification_repo,
)
from app.db.session import set_session_identity
from app.schemas.auth import LoginRequest, SignupRequest
from app.services import email_service


async def signup(
    session: AsyncSession,
    payload: SignupRequest,
    settings: Settings | None = None,
) -> User:
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

    # Establish trusted internal system context for registration under RLS
    await set_session_identity(session, role="system")

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

            # 6. Generate email verification token and queue verification email (T-10)
            raw_token = generate_action_token()
            token_hash = hash_action_token(raw_token)
            now = datetime.now(UTC)
            expires_at = now + timedelta(hours=24)

            await verification_repo.create_verification_token(
                session=session,
                user_id=user.id,
                token_hash=token_hash,
                expires_at=expires_at,
            )

            resolved_settings = settings or get_settings()
            email_service.queue_verification_email(
                session=session,
                recipient=user.email,
                recipient_name=payload.full_name,
                raw_token=raw_token,
                settings=resolved_settings,
                user_id=user.id,
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

    # Establish trusted internal system context for user credential lookup under RLS
    await set_session_identity(session, role="system")

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
            await set_session_identity(sec_session, role="system")
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

    # 6. Revalidate credentials have not changed, acquire row lock on users,
    # and record login timestamp
    updated = await user_repo.update_login_success(
        session, user.id, now, expected_password_hash=user.password_hash
    )
    if updated is False:
        raise AppError(
            code="AUTHENTICATION_FAILED",
            message="Email or password is incorrect.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    # 7. Session Rotation: If Web client presented an existing ambient cookie, revoke it
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

    # 8. Generate 32-byte cryptographic token and SHA-256 hash
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

    # 9. Persist session in PostgreSQL user_sessions
    user_session = await session_repo.create_session(
        session=session,
        user_id=user.id,
        token_hash=token_hash,
        expires_at=expires_at,
        absolute_expires_at=absolute_expires_at,
        created_at=now,
    )

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

    # Establish trusted internal system context for user credential lookup under RLS
    await set_session_identity(session, role="system")

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
            await set_session_identity(sec_session, role="system")
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
            await set_session_identity(sec_session, role="system")
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
    await set_session_identity(session, role="system")
    now = datetime.now(UTC)
    await session_repo.revoke_session(session, session_id, now, reason="logout")


async def revoke_all_sessions(
    session: AsyncSession,
    user_id: UUID,
    reason: str,
) -> int:
    """Revoke all active sessions for a user (e.g. password reset, suspension, ban)."""
    await set_session_identity(session, role="system")
    now = datetime.now(UTC)
    return await session_repo.revoke_all_user_sessions(session, user_id, now, reason)


async def get_user_profile(
    session: AsyncSession,
    user_id: UUID,
) -> tuple[User, UserProfile | None] | None:
    """Fetch user account and profile for /auth/me."""
    return await user_repo.get_user_with_profile(session, user_id)


async def verify_email(
    session: AsyncSession,
    token: str,
) -> User:
    """Verify user's email address using a single-use action token (T-10).

    Acceptance criteria:
    - Token works once; expired and reused tokens show clear messages.
    - Token hash is looked up in PostgreSQL.
    - If expired: raises TOKEN_EXPIRED (HTTP 400).
    - If already used: raises TOKEN_ALREADY_USED (HTTP 400).
    - If invalid or unknown: raises TOKEN_INVALID (HTTP 400).
    - If account disabled/suspended/banned: raises ACCOUNT_DISABLED (HTTP 403).
    - On success: marks token used and records email_verified_at.
    """
    await set_session_identity(session, role="system")
    clean_token = token.strip()
    if not clean_token or len(clean_token) < 10:
        raise AppError(
            code="TOKEN_INVALID",
            message="Invalid or malformed verification token.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    token_hash = hash_action_token(clean_token)
    token_row = await verification_repo.get_verification_token_by_hash(session, token_hash)

    if token_row is None:
        raise AppError(
            code="TOKEN_INVALID",
            message="Invalid or malformed verification token.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if token_row.used_at is not None:
        raise AppError(
            code="TOKEN_ALREADY_USED",
            message="This verification token has already been used.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    now = datetime.now(UTC)
    expires_at = (
        token_row.expires_at.replace(tzinfo=UTC)
        if token_row.expires_at.tzinfo is None
        else token_row.expires_at
    )

    if now >= expires_at:
        raise AppError(
            code="TOKEN_EXPIRED",
            message="This verification token has expired. Please request a new verification link.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    user = await user_repo.get_user_by_id(session, token_row.user_id)
    if user is None:
        raise AppError(
            code="USER_NOT_FOUND",
            message="User associated with this token was not found.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

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

    # Atomically mark token used and update email_verified_at
    marked = await verification_repo.mark_token_used(session, token_row.id, now)
    if not marked:
        # Atomic conditional update affected 0 rows, meaning a concurrent request
        # consumed the token or it expired at the boundary.
        # Fetch fresh database state with populate_existing=True to bypass identity map cache.
        fresh_token = await verification_repo.get_verification_token_by_hash(
            session, token_hash, populate_existing=True
        )
        if fresh_token is not None and fresh_token.used_at is not None:
            raise AppError(
                code="TOKEN_ALREADY_USED",
                message="This verification token has already been used.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        if fresh_token is not None and now >= (
            fresh_token.expires_at.replace(tzinfo=UTC)
            if fresh_token.expires_at.tzinfo is None
            else fresh_token.expires_at
        ):
            raise AppError(
                code="TOKEN_EXPIRED",
                message=(
                    "This verification token has expired. Please request a new verification link."
                ),
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        raise AppError(
            code="TOKEN_INVALID",
            message="Invalid or malformed verification token.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if user.email_verified_at is None:
        await user_repo.mark_user_email_verified(session, user.id, now)
        user.email_verified_at = now

    return user


async def resend_verification(
    session: AsyncSession,
    email: str,
    settings: Settings,
) -> None:
    """Resend email verification token (T-10).

    Acceptance criteria:
    - Does not leak whether the account exists (constant-time behavior, identical response).
    - If account exists and is unverified (and active):
      - Invalidates previous verification tokens.
      - Creates a new random token with 24-hour expiry.
      - Queues verification email in outbox.
    """
    await set_session_identity(session, role="system")
    clean_email = email.strip().lower()
    user = await user_repo.get_user_by_email_ci(session, clean_email)

    if user is None:
        # Prevent timing-based user enumeration
        verify_dummy_password()
        return

    # If user is suspended or banned, fail silently without leaking status
    if user.status != "active":
        verify_dummy_password()
        return

    # If user is already verified, do not send verification email; don't leak status
    if user.email_verified_at is not None:
        verify_dummy_password()
        return

    # User exists, active, and unverified: generate fresh token
    now = datetime.now(UTC)
    await verification_repo.invalidate_user_verification_tokens(session, user.id, now)

    raw_token = generate_action_token()
    token_hash = hash_action_token(raw_token)
    expires_at = now + timedelta(hours=24)

    await verification_repo.create_verification_token(
        session=session,
        user_id=user.id,
        token_hash=token_hash,
        expires_at=expires_at,
    )

    profile_row = await user_repo.get_user_with_profile(session, user.id)
    recipient_name = profile_row[1].full_name if profile_row and profile_row[1] else None

    email_service.queue_verification_email(
        session=session,
        recipient=user.email,
        recipient_name=recipient_name,
        raw_token=raw_token,
        settings=settings,
        user_id=user.id,
    )


async def forgot_password(
    session: AsyncSession,
    email: str,
    settings: Settings,
) -> None:
    """Request password reset token (T-11).

    Acceptance criteria:
    - Always responds 'If that email exists, we sent a link'.
    - Identical response and similar timing for known and unknown emails (anti-enumeration).
    - If account exists and is active:
      - Invalidates previous reset tokens.
      - Creates a new random single-use token with 30-minute expiry.
      - Queues password reset email in outbox.
    """
    await set_session_identity(session, role="system")
    clean_email = email.strip().lower()
    user = await user_repo.get_user_by_email_ci(session, clean_email)

    if user is None or user.status != "active":
        # Simulate token generation and hashing overhead to normalize execution path
        dummy_token = generate_action_token()
        _ = hash_action_token(dummy_token)
        return

    # User exists and is active: invalidate prior unused reset tokens
    now = datetime.now(UTC)
    await password_reset_repo.invalidate_user_reset_tokens(session, user.id, now)

    raw_token = generate_action_token()
    token_hash = hash_action_token(raw_token)
    expires_at = now + timedelta(minutes=30)

    await password_reset_repo.create_reset_token(
        session=session,
        user_id=user.id,
        token_hash=token_hash,
        expires_at=expires_at,
    )

    profile_row = await user_repo.get_user_with_profile(session, user.id)
    recipient_name = profile_row[1].full_name if profile_row and profile_row[1] else None

    email_service.queue_password_reset_email(
        session=session,
        recipient=user.email,
        recipient_name=recipient_name,
        raw_token=raw_token,
        settings=settings,
        user_id=user.id,
    )


async def reset_password(
    session: AsyncSession,
    token: str,
    new_password: str,
    request: Request | None = None,
    response: Response | None = None,
    settings: Settings | None = None,
) -> User:
    """Reset user password using single-use action token (T-11).

    Acceptance criteria:
    - Single-use hashed token with 30-minute expiry.
    - Token reuse and expiry are rejected with clear messages.
    - Password strength validated (min 10 chars, not in common password blocklist).
    - A successful reset revokes EVERY session of that user globally.
    - Ambient session cookie is cleared if present in response.
    """
    await set_session_identity(session, role="system")
    # 1. Validate new password strength upfront
    is_valid, msg = validate_password_strength(new_password)
    if not is_valid:
        raise AppError(
            code="WEAK_PASSWORD",
            message=msg or "Password does not meet security requirements.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # 2. Validate token format
    clean_token = token.strip()
    if not clean_token or len(clean_token) < 10:
        raise AppError(
            code="TOKEN_INVALID",
            message="Invalid or malformed reset token.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    token_hash = hash_action_token(clean_token)
    token_row = await password_reset_repo.get_reset_token_by_hash(session, token_hash)

    if token_row is None:
        raise AppError(
            code="TOKEN_INVALID",
            message="Invalid or malformed reset token.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if token_row.used_at is not None:
        raise AppError(
            code="TOKEN_ALREADY_USED",
            message="This password reset token has already been used.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    now = datetime.now(UTC)
    expires_at = (
        token_row.expires_at.replace(tzinfo=UTC)
        if token_row.expires_at.tzinfo is None
        else token_row.expires_at
    )

    if now >= expires_at:
        raise AppError(
            code="TOKEN_EXPIRED",
            message="This password reset token has expired. Please request a new reset link.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    user = await user_repo.get_user_by_id(session, token_row.user_id, for_update=True)
    if user is None:
        raise AppError(
            code="USER_NOT_FOUND",
            message="User associated with this token was not found.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

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

    # 3. Atomically mark token used enforcing single-use under concurrency
    marked = await password_reset_repo.mark_token_used(session, token_row.id, now)
    if not marked:
        fresh_token = await password_reset_repo.get_reset_token_by_hash(
            session, token_hash, populate_existing=True
        )
        if fresh_token is not None and fresh_token.used_at is not None:
            raise AppError(
                code="TOKEN_ALREADY_USED",
                message="This password reset token has already been used.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        if fresh_token is not None and now >= (
            fresh_token.expires_at.replace(tzinfo=UTC)
            if fresh_token.expires_at.tzinfo is None
            else fresh_token.expires_at
        ):
            raise AppError(
                code="TOKEN_EXPIRED",
                message="This password reset token has expired. Please request a new reset link.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        raise AppError(
            code="TOKEN_INVALID",
            message="Invalid or malformed reset token.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # 4. Hash new password with Argon2id and update user credentials
    new_password_hash = hash_password(new_password)
    await user_repo.update_user_password(session, user.id, new_password_hash)

    # 5. Invalidate all other outstanding unexpired reset tokens for this user
    await password_reset_repo.invalidate_user_reset_tokens(session, user.id, now)

    # 6. Global session revocation: revoke all active sessions for this user
    await session_repo.revoke_all_user_sessions(
        session=session,
        user_id=user.id,
        revoked_at=now,
        reason="password_reset",
    )

    # 6. Clear ambient session cookie if present
    if request is not None and response is not None and settings is not None:
        if request.cookies.get(settings.session_cookie_name) is not None:
            response.delete_cookie(
                key=settings.session_cookie_name,
                path="/",
                httponly=True,
                secure=settings.is_production or (settings.app_env != "development"),
                samesite="lax",
            )

    return user

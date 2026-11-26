"""CSRF and Origin protection module (T-07).

Provides cryptographic CSRF token generation, session binding, anonymous bootstrap,
Web Origin validation, and strict native bearer exemption.
"""

import hashlib
import hmac
import logging
import secrets
import time
from typing import Annotated

from fastapi import Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.db.session import get_session, get_session_factory
from app.services import auth_service

logger = logging.getLogger(__name__)

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
PRE_AUTH_PATHS = frozenset(
    {
        "/api/v1/auth/signup",
        "/api/v1/auth/login",
    }
)


def get_csrf_signing_key(settings: Settings) -> bytes:
    """Return HMAC key for signing CSRF tokens.

    Uses settings.csrf_secret_key if configured; otherwise derives a deterministic
    key from the database URL for development and testing environments.
    Strictly fails closed in production and staging if auth endpoints are active without
    an explicit CSRF secret key.
    """
    if settings.csrf_secret_key and settings.csrf_secret_key.strip():
        return settings.csrf_secret_key.strip().encode("utf-8")
    if settings.app_env in ("production", "staging") and settings.auth_endpoints_enabled:
        raise RuntimeError("CSRF_SECRET_KEY_REQUIRED_IN_PRODUCTION")
    return hashlib.sha256(f"dormiostay-csrf-salt:{settings.database_url}".encode()).digest()


def generate_anonymous_csrf_token(secret_key: bytes, expire_seconds: int = 3600) -> str:
    """Generate a short-lived anonymous CSRF bootstrap token.

    Format: anon.<issued_at>.<nonce>.<signature>
    """
    issued_at = int(time.time())
    nonce = secrets.token_hex(8)
    message = f"anon:{issued_at}:{nonce}".encode()
    signature = hmac.new(secret_key, message, hashlib.sha256).hexdigest()
    return f"anon.{issued_at}.{nonce}.{signature}"


def verify_anonymous_csrf_token(
    token: str, secret_key: bytes, expire_seconds: int = 3600
) -> tuple[bool, str]:
    """Verify an anonymous CSRF bootstrap token.

    Returns (is_valid, error_code).
    """
    parts = token.split(".")
    if len(parts) != 4 or parts[0] != "anon":
        return False, "CSRF_TOKEN_INVALID"

    _, issued_at_str, nonce, signature = parts
    try:
        issued_at = int(issued_at_str)
    except ValueError:
        return False, "CSRF_TOKEN_INVALID"

    now = int(time.time())
    if now - issued_at > expire_seconds:
        return False, "CSRF_TOKEN_EXPIRED"
    if issued_at - now > 60:
        return False, "CSRF_TOKEN_INVALID"

    message = f"anon:{issued_at}:{nonce}".encode()
    expected_sig = hmac.new(secret_key, message, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected_sig):
        return False, "CSRF_TOKEN_INVALID"

    return True, ""


def generate_session_csrf_token(
    session_token_hash: str, secret_key: bytes, expire_seconds: int = 3600
) -> str:
    """Generate a CSRF token cryptographically bound to an authenticated session.

    Format: sess.<issued_at>.<nonce>.<signature>
    The signature is computed over sess:<session_token_hash>:<issued_at>:<nonce>,
    binding the token to the specific session row.
    """
    issued_at = int(time.time())
    nonce = secrets.token_hex(8)
    message = f"sess:{session_token_hash}:{issued_at}:{nonce}".encode()
    signature = hmac.new(secret_key, message, hashlib.sha256).hexdigest()
    return f"sess.{issued_at}.{nonce}.{signature}"


def verify_session_csrf_token(
    token: str, session_token_hash: str, secret_key: bytes, expire_seconds: int = 3600
) -> tuple[bool, str]:
    """Verify that a CSRF token is valid and cryptographically bound to the session.

    Returns (is_valid, error_code).
    """
    parts = token.split(".")
    if len(parts) != 4 or parts[0] != "sess":
        return False, "CSRF_TOKEN_INVALID"

    _, issued_at_str, nonce, signature = parts
    try:
        issued_at = int(issued_at_str)
    except ValueError:
        return False, "CSRF_TOKEN_INVALID"

    now = int(time.time())
    if now - issued_at > expire_seconds:
        return False, "CSRF_TOKEN_EXPIRED"
    if issued_at - now > 60:
        return False, "CSRF_TOKEN_INVALID"

    message = f"sess:{session_token_hash}:{issued_at}:{nonce}".encode()
    expected_sig = hmac.new(secret_key, message, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected_sig):
        return False, "CSRF_TOKEN_INVALID"

    return True, ""


def verify_web_origin(request: Request, settings: Settings) -> None:
    """Enforce exact, approved Web Origin on browser write requests.

    Rejects missing, null, or foreign Origins.
    """
    origin = request.headers.get("Origin")
    if not origin or not origin.strip():
        raise AppError(
            code="FORBIDDEN_ORIGIN",
            message="Origin header is required for Web write requests.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    normalized_origin = origin.strip().rstrip("/").lower()
    expected_origin = str(settings.frontend_origin).rstrip("/").lower()

    if normalized_origin == "null" or normalized_origin != expected_origin:
        raise AppError(
            code="FORBIDDEN_ORIGIN",
            message="Disallowed Origin.",
            status_code=status.HTTP_403_FORBIDDEN,
        )


async def verify_csrf(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    db_session: Annotated[AsyncSession, Depends(get_session)],
    session_factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)],
) -> None:
    """FastAPI dependency enforcing CSRF and Origin protection on state-changing requests.

    Invariants:
    1. Safe methods (GET, HEAD, OPTIONS) are exempt.
    2. Disabled via settings.csrf_protection_enabled (e.g. for controlled testing).
    3. Provider webhooks (/api/v1/webhooks/*) retain their own signature authentication.
    4. Mixed credentials (cookie + Authorization) fail immediately (400 AMBIGUOUS_CREDENTIALS).
    5. Native Bearer: Successfully validated, cookie-free Bearer requests are exempt from CSRF.
       Invalid or expired Bearer requests fail with 401 and are NEVER exempt.
    6. Native Pre-Auth: Explicit native mode (X-Client-Type: native), JSON content type,
       no session cookie, and NO browser Origin.
    7. Web Writes (Cookie or Anonymous): Require exact approved Web Origin and valid X-CSRF-Token.
       Cookie writes require session-bound token; anonymous writes require anonymous token.
    """
    # 1. Safe methods are unaffected
    if request.method in SAFE_METHODS:
        return

    # 2. Check if CSRF protection is globally disabled (controlled test toggle)
    if not settings.csrf_protection_enabled:
        return

    # 3. Provider webhooks use signature/secret authentication, not CSRF
    if request.url.path.startswith("/api/v1/webhooks"):
        return

    cookie_token: str | None = request.cookies.get(settings.session_cookie_name)
    auth_header: str | None = request.headers.get("Authorization")
    client_type_header: str | None = request.headers.get("X-Client-Type")
    csrf_token_header: str | None = request.headers.get("X-CSRF-Token")

    # 4. Reject mixed credentials (both cookie and Authorization header)
    if cookie_token is not None and auth_header is not None:
        raise AppError(
            code="AMBIGUOUS_CREDENTIALS",
            message=(
                "Ambiguous authentication credentials: both session cookie "
                "and Authorization header provided."
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    signing_key = get_csrf_signing_key(settings)

    # 5. Native Bearer Request
    if auth_header is not None:
        parts = auth_header.split(" ", 1)
        if len(parts) != 2 or parts[0].lower() != "bearer":
            raise AppError(
                code="INVALID_AUTHORIZATION_HEADER",
                message="Invalid Authorization header format. Must be 'Bearer <session_token>'.",
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
        raw_token = parts[1].strip()
        if not raw_token:
            raise AppError(
                code="EMPTY_BEARER_TOKEN",
                message="Bearer token cannot be empty.",
                status_code=status.HTTP_401_UNAUTHORIZED,
            )

        # Only successfully validated bearer requests are exempt; never exempt invalid bearer
        user_session, user = await auth_service.validate_session(
            session=db_session,
            raw_token=raw_token,
            settings=settings,
            session_factory=session_factory,
        )
        # Store on request.state for downstream dependencies
        request.state.user_session = user_session
        request.state.user = user
        return

    # 6. Check for Native Pre-Authentication (e.g. native login)
    is_native_header = (
        client_type_header is not None and client_type_header.strip().lower() == "native"
    )

    if is_native_header:
        # Native client marker with ambient session cookie is forbidden
        if cookie_token is not None:
            raise AppError(
                code="AMBIGUOUS_CREDENTIALS",
                message="Native authentication must not include session cookies.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        # Reject browser Origin on native pre-authentication requests
        if request.headers.get("Origin") is not None:
            raise AppError(
                code="NATIVE_ORIGIN_PROHIBITED",
                message="Browser Origin is prohibited on native pre-authentication requests.",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        # Content-Type must be application/json
        content_type = request.headers.get("Content-Type", "").strip().lower()
        if not content_type.startswith("application/json"):
            raise AppError(
                code="INVALID_CONTENT_TYPE",
                message="Content-Type must be application/json for native requests.",
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            )

        # Only documented pre-auth paths are eligible for native pre-auth exemption
        normalized_path = request.url.path.rstrip("/")
        if normalized_path in PRE_AUTH_PATHS:
            return

        # Any other native request without Bearer token requires credentials
        raise AppError(
            code="UNAUTHORIZED",
            message="Authentication credentials required.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    # 7. Web Requests: Enforce exact, approved Web Origin
    verify_web_origin(request, settings)

    # 8. Web Requests: Enforce X-CSRF-Token presence
    if not csrf_token_header or not csrf_token_header.strip():
        raise AppError(
            code="CSRF_TOKEN_MISSING",
            message="CSRF token is required.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    clean_csrf = csrf_token_header.strip()

    # 9. Cookie-Authenticated Web Requests
    if cookie_token is not None:
        user_session, user = await auth_service.validate_session(
            session=db_session,
            raw_token=cookie_token.strip(),
            settings=settings,
            session_factory=session_factory,
        )
        request.state.user_session = user_session
        request.state.user = user

        # Verify session-bound CSRF token
        is_valid, err_code = verify_session_csrf_token(
            token=clean_csrf,
            session_token_hash=user_session.token_hash,
            secret_key=signing_key,
            expire_seconds=settings.csrf_token_expire_seconds,
        )

        # On login with ambient cookie, also allow valid anonymous bootstrap token
        if not is_valid and request.url.path.rstrip("/").endswith("/login"):
            anon_valid, _ = verify_anonymous_csrf_token(
                token=clean_csrf,
                secret_key=signing_key,
                expire_seconds=settings.csrf_token_expire_seconds,
            )
            if anon_valid:
                is_valid = True

        if not is_valid:
            raise AppError(
                code=err_code,
                message="Invalid or expired CSRF token.",
                status_code=status.HTTP_403_FORBIDDEN,
            )
        return

    # 10. Anonymous Web Writes (e.g. signup, web login)
    is_valid, err_code = verify_anonymous_csrf_token(
        token=clean_csrf,
        secret_key=signing_key,
        expire_seconds=settings.csrf_token_expire_seconds,
    )
    if not is_valid:
        raise AppError(
            code=err_code,
            message="Invalid or expired CSRF token.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

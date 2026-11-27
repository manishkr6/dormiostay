"""FastAPI authorization dependencies and role-based access control (T-12).

Authoritative References:
- Feature Ticket List.md — T-12 (Role permissions and row-level security).
- Security and Access Document — Section 3 (Access rules) and Section 4 (RLS).
- Technical Architecture Document — Section 5.1 & Section 5.8.

Dependencies provided:
- require_user: Enforces authenticated, active user. Rejects guest (401) and suspended/banned (403).
- require_verified: Enforces authenticated user with verified email address.
- require_role(*roles): Checks user role against server-validated session (rejects spoofed claims).
- require_approved_owner: Enforces owner role and verified approved host application.
- require_admin: Enforces admin role and preserves future T-13 MFA boundary.
"""

from collections.abc import Callable, Coroutine
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_session_and_user
from app.core.errors import AppError
from app.db.models import User, UserSession
from app.db.session import get_session


async def require_user(
    session_and_user: Annotated[tuple[UserSession, User], Depends(get_current_session_and_user)],
) -> User:
    """Require an authenticated user with an active account status.

    Rejection behavior:
    - Unauthenticated: Handled by get_current_session_and_user -> HTTP 401 UNAUTHORIZED.
    - Suspended / Banned / Inactive: HTTP 403 FORBIDDEN with code ACCOUNT_DISABLED.
    """
    _, user = session_and_user
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
    return user


async def require_verified(
    user: Annotated[User, Depends(require_user)],
) -> User:
    """Require an authenticated user who has completed email verification.

    Unverified users may log in and browse public resources, but cannot perform
    restricted actions (e.g. booking visits, listing properties).
    """
    if user.email_verified_at is None:
        raise AppError(
            code="EMAIL_NOT_VERIFIED",
            message=(
                "Email verification is required to perform this action. Please verify your email."
            ),
            status_code=status.HTTP_403_FORBIDDEN,
        )
    return user


def require_role(*allowed_roles: str) -> Callable[..., Coroutine[Any, Any, User]]:
    """Dependency factory checking that the authenticated user possesses an allowed role.

    Strict security invariants:
    - Inspects server-validated user.role from the active database session.
    - Never trusts client-supplied headers, request bodies, or unverified claims.
    - Prevents privilege escalation.
    """

    async def _role_checker(
        user: Annotated[User, Depends(require_user)],
    ) -> User:
        if user.role not in allowed_roles:
            raise AppError(
                code="FORBIDDEN_ROLE",
                message=f"Access denied. Required role: {', '.join(allowed_roles)}.",
                status_code=status.HTTP_403_FORBIDDEN,
            )
        return user

    return _role_checker


async def is_owner_approved(session: AsyncSession, user_id: UUID) -> bool:
    """Check whether an owner user has an approved host application.

    Interim implementation pending Feature Ticket T-16 (Host Application & Onboarding Flow):
    The host_applications table is scheduled for T-16 and does not exist in the 0001-0003 schema.
    To avoid executing SQL against a non-existent table (which poisons PostgreSQL transactions
    with SQLSTATE 42P01 UndefinedTable), this check fails closed and returns False without
    executing SQL.
    Once T-16 creates host_applications, this function will query host_applications
    for status='approved'.
    """
    # Fail closed safely without poisoning PostgreSQL transactions with 42P01 (UndefinedTable)
    return False


async def require_approved_owner(
    user: Annotated[User, Depends(require_user)],
    db_session: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    """Require an authenticated user who is both an owner and has an approved host application.

    Strict security invariants:
    - Non-owners are rejected with HTTP 403 FORBIDDEN_ROLE.
    - Email verification alone is NOT proof of ownership approval.
    - Pending, draft, submitted, needs_info, rejected, or missing applications fail closed.
    """
    if user.role != "owner":
        raise AppError(
            code="FORBIDDEN_ROLE",
            message="Only owners may access this resource.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    # Verify approved ownership status in host_applications
    approved = await is_owner_approved(db_session, user.id)
    if not approved:
        raise AppError(
            code="OWNER_NOT_APPROVED",
            message=(
                "Owner application is not approved. Only approved owners may perform this action."
            ),
            status_code=status.HTTP_403_FORBIDDEN,
        )

    return user


async def require_admin(
    user: Annotated[User, Depends(require_user)],
    request: Request,
) -> User:
    """Require an authenticated administrator, preserving the future T-13 MFA boundary.

    Strict security invariants:
    - Non-admins are rejected with HTTP 403 FORBIDDEN_ROLE.
    - An admin role is NOT silently treated as proof of completed MFA when MFA enforcement
      is active (checked via request.state.mfa_completed, defaulting to open until T-13).
    """
    if user.role != "admin":
        raise AppError(
            code="FORBIDDEN_ROLE",
            message="Administrator privileges required.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    # Future T-13 Admin MFA boundary preservation:
    # Operations requiring completed MFA must explicitly verify that mfa_completed is True.
    # Fail closed on missing, None, False, or untrusted state.
    mfa_completed = getattr(request.state, "mfa_completed", None)
    if mfa_completed is not True:
        raise AppError(
            code="MFA_REQUIRED",
            message="MFA verification is required for administrative operations.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    return user

"""Repository for UserSession database operations."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User, UserSession


async def create_session(
    session: AsyncSession,
    user_id: UUID,
    token_hash: str,
    expires_at: datetime,
    absolute_expires_at: datetime,
    created_at: datetime | None = None,
) -> UserSession:
    """Create a new server-side user session."""
    user_session = UserSession(
        user_id=user_id,
        token_hash=token_hash,
        expires_at=expires_at,
        absolute_expires_at=absolute_expires_at,
    )
    if created_at is not None:
        user_session.last_activity_at = created_at
    session.add(user_session)
    await session.flush()
    return user_session


async def get_session_with_user_by_token_hash(
    session: AsyncSession,
    token_hash: str,
) -> tuple[UserSession, User] | None:
    """Retrieve active or revoked session joined with user by SHA-256 token hash."""
    stmt = (
        select(UserSession, User)
        .join(User, UserSession.user_id == User.id)
        .where(UserSession.token_hash == token_hash)
    )
    result = await session.execute(stmt)
    row = result.first()
    if row is None:
        return None
    return (row[0], row[1])


async def update_session_activity(
    session: AsyncSession,
    session_id: UUID,
    last_activity_at: datetime,
    new_expires_at: datetime,
) -> bool:
    """Update sliding idle expiry and last activity timestamp only if unrevoked.

    Returns True if the session was successfully updated, or False if the session
    was already revoked (e.g. concurrent logout race).
    """
    stmt = (
        update(UserSession)
        .where(
            UserSession.id == session_id,
            UserSession.revoked_at.is_(None),
        )
        .values(
            last_activity_at=last_activity_at,
            expires_at=new_expires_at,
        )
        .returning(UserSession.id)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


async def revoke_session(
    session: AsyncSession,
    session_id: UUID,
    revoked_at: datetime,
    reason: str = "logout",
) -> None:
    """Revoke a single session by primary key ID."""
    stmt = (
        update(UserSession)
        .where(UserSession.id == session_id)
        .values(
            revoked_at=revoked_at,
            revocation_reason=reason,
        )
    )
    await session.execute(stmt)


async def revoke_all_user_sessions(
    session: AsyncSession,
    user_id: UUID,
    revoked_at: datetime,
    reason: str,
) -> int:
    """Revoke all unrevoked sessions for a specific user.

    Used on password reset, account suspension, ban, or security logout-all events.
    Returns the number of revoked sessions.
    """
    stmt = (
        update(UserSession)
        .where(
            UserSession.user_id == user_id,
            UserSession.revoked_at.is_(None),
        )
        .values(
            revoked_at=revoked_at,
            revocation_reason=reason,
        )
    )
    result = await session.execute(stmt)
    return int(result.rowcount)  # type: ignore[attr-defined]

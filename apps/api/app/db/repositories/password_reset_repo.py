"""Repository for PasswordResetToken database operations (T-11)."""

import inspect
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PasswordResetToken


async def create_reset_token(
    session: AsyncSession,
    user_id: UUID,
    token_hash: str,
    expires_at: datetime,
) -> PasswordResetToken:
    """Insert a new password reset token record."""
    token = PasswordResetToken(
        user_id=user_id,
        token_hash=token_hash,
        expires_at=expires_at,
    )
    add_fn: Any = getattr(session, "add", None)
    if callable(add_fn):
        res: Any = add_fn(token)
        if inspect.iscoroutine(res):
            res.close()
    await session.flush()
    return token


async def get_reset_token_by_hash(
    session: AsyncSession,
    token_hash: str,
    *,
    populate_existing: bool = False,
) -> PasswordResetToken | None:
    """Retrieve a password reset token by its SHA-256 hash.

    If populate_existing=True, forces SQLAlchemy to refresh the entity's attributes
    from the database even if the entity is already present in the AsyncSession identity map.
    """
    stmt = select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
    if populate_existing:
        stmt = stmt.execution_options(populate_existing=True)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def mark_token_used(
    session: AsyncSession,
    token_id: UUID,
    now: datetime,
) -> bool:
    """Atomically mark an unexpired, unused password reset token as used.

    Returns True if exactly one row was updated (successful single-use consumption),
    or False if the token was already used or has expired.
    Disables local ORM synchronize_session to prevent phantom in-memory attribute
    mutations on failed updates.
    """
    stmt = (
        update(PasswordResetToken)
        .where(
            PasswordResetToken.id == token_id,
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.expires_at > now,
        )
        .values(used_at=now)
        .execution_options(synchronize_session=False)
    )
    result = await session.execute(stmt)
    return int(getattr(result, "rowcount", 0) or 0) == 1


async def invalidate_user_reset_tokens(
    session: AsyncSession,
    user_id: UUID,
    now: datetime,
) -> int:
    """Invalidate all unexpired, unused reset tokens for a user by setting used_at."""
    stmt = (
        update(PasswordResetToken)
        .where(
            PasswordResetToken.user_id == user_id,
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.expires_at > now,
        )
        .values(used_at=now)
        .execution_options(synchronize_session=False)
    )
    result = await session.execute(stmt)
    return int(getattr(result, "rowcount", 0) or 0)

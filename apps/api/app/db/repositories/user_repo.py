"""Repository for User and UserProfile database operations."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User, UserProfile


async def get_user_by_id(
    session: AsyncSession,
    user_id: UUID,
    *,
    for_update: bool = False,
) -> User | None:
    """Retrieve user by primary key UUID."""
    stmt = select(User).where(User.id == user_id)
    if for_update:
        stmt = stmt.with_for_update()
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_user_by_email_ci(session: AsyncSession, email: str) -> User | None:
    """Retrieve user by email using case-insensitive comparison (lower(email))."""
    normalized_email = email.strip().lower()
    stmt = select(User).where(func.lower(User.email) == normalized_email)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_user_with_profile(
    session: AsyncSession, user_id: UUID
) -> tuple[User, UserProfile | None] | None:
    """Retrieve user and associated profile by user_id."""
    stmt = (
        select(User, UserProfile)
        .outerjoin(UserProfile, User.id == UserProfile.user_id)
        .where(User.id == user_id)
    )
    result = await session.execute(stmt)
    row = result.first()
    if row is None:
        return None
    return (row[0], row[1])


async def create_user(
    session: AsyncSession,
    email: str,
    password_hash: str,
    role: str = "tenant",
    status: str = "active",
) -> User:
    """Insert a new user record."""
    user = User(
        email=email.strip().lower(),
        password_hash=password_hash,
        role=role,
        status=status,
    )
    session.add(user)
    await session.flush()
    return user


async def create_user_profile(
    session: AsyncSession,
    user_id: UUID,
    full_name: str,
    phone: str | None = None,
    institution_id: UUID | None = None,
) -> UserProfile:
    """Insert a new user profile record."""
    profile = UserProfile(
        user_id=user_id,
        full_name=full_name.strip(),
        phone=phone.strip() if phone else None,
        institution_id=institution_id,
        preferred_areas=[],
    )
    session.add(profile)
    await session.flush()
    return profile


async def update_login_success(
    session: AsyncSession,
    user_id: UUID,
    login_at: datetime,
    expected_password_hash: str | None = None,
) -> bool:
    """Reset failed login count and record last login timestamp.

    If expected_password_hash is provided, conditionally updates only if the user's
    password_hash has not changed during authentication (optimistic concurrency guard against
    concurrent password reset). Returns True if rowcount == 1, or False if credentials changed.
    """
    stmt = update(User).where(User.id == user_id)
    if expected_password_hash is not None:
        stmt = stmt.where(User.password_hash == expected_password_hash)
    stmt = stmt.values(failed_login_count=0, last_login_at=login_at)
    result = await session.execute(stmt)
    return int(getattr(result, "rowcount", 0) or 0) == 1


async def update_login_failure(
    session: AsyncSession,
    user_id: UUID,
) -> None:
    """Increment failed login count on incorrect password."""
    stmt = (
        update(User)
        .where(User.id == user_id)
        .values(failed_login_count=User.failed_login_count + 1)
    )
    await session.execute(stmt)


async def mark_user_email_verified(
    session: AsyncSession,
    user_id: UUID,
    verified_at: datetime,
) -> None:
    """Record email verification timestamp on the user."""
    stmt = update(User).where(User.id == user_id).values(email_verified_at=verified_at)
    await session.execute(stmt)


async def update_user_password(
    session: AsyncSession,
    user_id: UUID,
    new_password_hash: str,
) -> None:
    """Update user password hash and reset failed login count (T-11)."""
    stmt = (
        update(User)
        .where(User.id == user_id)
        .values(
            password_hash=new_password_hash,
            failed_login_count=0,
            locked_until=None,
        )
    )
    await session.execute(stmt)

"""Repository for User and UserProfile database operations."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User, UserProfile


async def get_user_by_id(session: AsyncSession, user_id: UUID) -> User | None:
    """Retrieve user by primary key UUID."""
    stmt = select(User).where(User.id == user_id)
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
) -> None:
    """Reset failed login count and record last login timestamp."""
    stmt = (
        update(User).where(User.id == user_id).values(failed_login_count=0, last_login_at=login_at)
    )
    await session.execute(stmt)


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

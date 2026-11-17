"""T-03 foundation models; later-ticket tables are deliberately absent."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base as Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('tenant', 'owner', 'admin')", name="role"),
        CheckConstraint("status IN ('active', 'suspended', 'banned')", name="status"),
        CheckConstraint("failed_login_count >= 0", name="failed_login_count"),
        Index("uq_users_email_ci", func.lower(text("email")), unique=True),
    )
    email: Mapped[str] = mapped_column(Text)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text, server_default="tenant")
    status: Mapped[str] = mapped_column(Text, server_default="active")
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_login_count: Mapped[int] = mapped_column(Integer, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    profile: Mapped["UserProfile | None"] = relationship(back_populates="user", lazy="raise")
    sessions: Mapped[list["UserSession"]] = relationship(back_populates="user", lazy="raise")
    verification_tokens: Mapped[list["EmailVerificationToken"]] = relationship(lazy="raise")
    reset_tokens: Mapped[list["PasswordResetToken"]] = relationship(lazy="raise")


class Location(Base):
    __tablename__ = "locations"
    __table_args__ = (
        CheckConstraint("latitude BETWEEN -90 AND 90", name="latitude"),
        CheckConstraint("longitude BETWEEN -180 AND 180", name="longitude"),
        CheckConstraint(
            "type IN ('country','state','district','city','neighbourhood')", name="type"
        ),
        CheckConstraint("parent_id IS NULL OR parent_id <> id", name="parent"),
        Index(
            "ix_locations_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
    )
    name: Mapped[str] = mapped_column(Text)
    type: Mapped[str] = mapped_column(Text)
    parent_id: Mapped[UUID | None] = mapped_column(ForeignKey("locations.id"), index=True)
    latitude: Mapped[float | None]
    longitude: Mapped[float | None]
    aliases: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    display_order: Mapped[int] = mapped_column(Integer, server_default="0")
    parent: Mapped["Location | None"] = relationship(remote_side="Location.id", lazy="raise")
    institutions: Mapped[list["Institution"]] = relationship(
        back_populates="location", lazy="raise"
    )


class Institution(Base):
    __tablename__ = "institutions"
    __table_args__ = (
        CheckConstraint("latitude BETWEEN -90 AND 90", name="latitude"),
        CheckConstraint("longitude BETWEEN -180 AND 180", name="longitude"),
    )
    name: Mapped[str] = mapped_column(Text)
    aliases: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    location_id: Mapped[UUID] = mapped_column(ForeignKey("locations.id"), index=True)
    latitude: Mapped[float | None]
    longitude: Mapped[float | None]
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    location: Mapped[Location] = relationship(back_populates="institutions", lazy="raise")


Index(
    "ix_locations_aliases_trgm",
    func.dormiostay_alias_text(Location.aliases).label("aliases_text"),
    postgresql_using="gin",
    postgresql_ops={"aliases_text": "gin_trgm_ops"},
)


class UserProfile(Base):
    __tablename__ = "user_profiles"
    __table_args__ = (
        CheckConstraint("preferred_budget_min >= 0", name="budget_min"),
        CheckConstraint("preferred_budget_max >= preferred_budget_min", name="budget_range"),
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), unique=True)
    full_name: Mapped[str] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    avatar_url: Mapped[str | None] = mapped_column(Text)
    gender: Mapped[str | None] = mapped_column(Text)
    preferred_budget_min: Mapped[int | None] = mapped_column(Integer)
    preferred_budget_max: Mapped[int | None] = mapped_column(Integer)
    preferred_areas: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    institution_id: Mapped[UUID | None] = mapped_column(ForeignKey("institutions.id"), index=True)
    user: Mapped[User] = relationship(back_populates="profile", lazy="raise")
    institution: Mapped[Institution | None] = relationship(lazy="raise")


class UserSession(Base):
    __tablename__ = "user_sessions"
    __table_args__ = (CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="token_hash"),)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(Text, unique=True)
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    absolute_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revocation_reason: Mapped[str | None] = mapped_column(Text)
    user: Mapped[User] = relationship(back_populates="sessions", lazy="raise")


class TokenColumns:
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(Text, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EmailVerificationToken(TokenColumns, Base):
    __tablename__ = "email_verification_tokens"
    __table_args__ = (CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="token_hash"),)


class PasswordResetToken(TokenColumns, Base):
    __tablename__ = "password_reset_tokens"
    __table_args__ = (CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="token_hash"),)


class Amenity(Base):
    __tablename__ = "amenities"
    name: Mapped[str] = mapped_column(Text, unique=True)
    icon: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


class PropertyCategory(Base):
    __tablename__ = "property_categories"
    name: Mapped[str] = mapped_column(Text, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


class AuditLog(Base):
    __tablename__ = "audit_logs"
    actor_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(Text)
    target_type: Mapped[str] = mapped_column(Text)
    target_id: Mapped[UUID | None]
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(INET)
    actor: Mapped[User | None] = relationship(lazy="raise")


class PlatformSetting(Base):
    __tablename__ = "platform_settings"
    key: Mapped[str] = mapped_column(Text, unique=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB)

"""Pydantic schemas for authentication and session validation."""

import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SignupRequest(BaseModel):
    """Public account registration payload. Only Tenant and Owner roles permitted."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(..., max_length=255, description="User email address")
    password: str = Field(..., min_length=1, description="Plaintext password")
    role: str = Field(..., description="Public registration role; admin is strictly rejected")
    full_name: str = Field(..., min_length=1, max_length=200, description="Full name for profile")
    phone: str | None = Field(default=None, max_length=30, description="Optional contact phone")
    institution_id: UUID | None = Field(
        default=None, description="Optional associated institution UUID"
    )

    @field_validator("email", mode="before")
    @classmethod
    def strip_email(cls, value: str) -> str:
        return value.strip() if isinstance(value, str) else value

    @field_validator("full_name", mode="before")
    @classmethod
    def strip_full_name(cls, value: str) -> str:
        return value.strip() if isinstance(value, str) else value

    @field_validator("phone", mode="before")
    @classmethod
    def strip_phone(cls, value: str | None) -> str | None:
        return value.strip() if isinstance(value, str) else value

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        cleaned = value.strip()
        if not EMAIL_PATTERN.match(cleaned):
            raise ValueError("Invalid email address format.")
        return cleaned


class LoginRequest(BaseModel):
    """Authentication login payload for Web and Native Mobile clients."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(..., max_length=255, description="User email address")
    password: str = Field(..., min_length=1, description="Account password")
    client_type: Literal["web", "native"] = Field(
        default="web",
        description=(
            "Explicit client transport mode: 'web' issues HttpOnly cookie; "
            "'native' issues opaque token in body"
        ),
    )

    @field_validator("email", mode="before")
    @classmethod
    def strip_email(cls, value: str) -> str:
        return value.strip() if isinstance(value, str) else value

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        cleaned = value.strip()
        if not EMAIL_PATTERN.match(cleaned):
            raise ValueError("Invalid email address format.")
        return cleaned


class UserResponse(BaseModel):
    """Safe user account representation without sensitive security attributes."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    role: str
    status: str
    email_verified: bool
    created_at: datetime


class ProfileResponse(BaseModel):
    """Safe user profile representation."""

    model_config = ConfigDict(from_attributes=True)

    full_name: str
    phone: str | None = None
    avatar_url: str | None = None
    gender: str | None = None
    preferred_budget_min: int | None = None
    preferred_budget_max: int | None = None
    preferred_areas: list[str] = Field(default_factory=list)
    institution_id: UUID | None = None


class MeResponse(BaseModel):
    """Current authenticated user and profile information."""

    user: UserResponse
    profile: ProfileResponse | None = None


class AuthResponseWeb(BaseModel):
    """Web login response payload. Token is transported exclusively via HttpOnly cookie."""

    user: UserResponse


class AuthResponseNative(BaseModel):
    """Native mobile login response payload. Token is returned once for SecureStore."""

    token: str
    user: UserResponse


class StatusResponse(BaseModel):
    """Generic status response."""

    status: str = "ok"

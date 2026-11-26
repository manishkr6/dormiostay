"""Cryptographic security utilities for passwords and server-side sessions."""

import hashlib
import secrets
from typing import Final

from pwdlib import PasswordHash

# Initialize pwdlib with recommended Argon2id parameters
_password_hash = PasswordHash.recommended()

# Fixed precomputed dummy Argon2id hash for constant-time failure when email is not found
_DUMMY_ARGON2_HASH: Final[str] = (
    "$argon2id$v=19$m=65536,t=3,p=4$c29tZXNhbHQxMjM0NTY3OA$q1+0m3C815qYq2XhBq1Gf9m9dC3G1y6r2Q8t7H3j4l8"
)

# Documented common passwords to block (must be checked case-insensitively)
COMMON_PASSWORDS: Final[frozenset[str]] = frozenset(
    [
        "password123",
        "password1234",
        "password12345",
        "1234567890",
        "12345678901",
        "123456789012",
        "dormiostay123",
        "dormiostay2026",
        "letmein1234",
        "welcome1234",
        "welcome12345",
        "admin12345",
        "admin123456",
        "qwerty12345",
        "iloveyou123",
        "changeme123",
        "testing1234",
        "sikkim12345",
        "gangtok12345",
    ]
)

MIN_PASSWORD_LENGTH: Final[int] = 10


def hash_password(password: str) -> str:
    """Hash plaintext password using pwdlib Argon2id."""
    return _password_hash.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify plaintext password against an Argon2id hash."""
    try:
        return _password_hash.verify(plain_password, hashed_password)
    except Exception:
        return False


def verify_dummy_password() -> None:
    """Execute a dummy password verification to mitigate timing attacks on nonexistent users."""
    try:
        _password_hash.verify("dummy_password_timing_pad_1234", _DUMMY_ARGON2_HASH)
    except Exception:
        pass


def validate_password_strength(password: str) -> tuple[bool, str | None]:
    """Validate that password meets minimum length and is not in the common password blocklist.

    Returns:
        tuple[bool, str | None]: (is_valid, error_message)
    """
    if len(password) < MIN_PASSWORD_LENGTH:
        return (
            False,
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters long.",
        )

    norm_pwd = password.strip().lower()
    if norm_pwd in COMMON_PASSWORDS:
        return (
            False,
            "The password chosen is too common. Please choose a stronger passphrase.",
        )

    if len(set(norm_pwd)) <= 1:
        return (
            False,
            "Password cannot consist of a single repeated character.",
        )

    return (True, None)


def generate_session_token() -> str:
    """Generate a cryptographically secure opaque session token with at least 32 random bytes."""
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    """Compute the SHA-256 hex digest of a session token for storage in PostgreSQL.

    Conforms to the ck_user_sessions_token_hash constraint: ^[0-9a-f]{64}$
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_action_token() -> str:
    """Generate a cryptographically secure opaque action token for verification or reset."""
    return secrets.token_urlsafe(32)


def hash_action_token(token: str) -> str:
    """Compute the SHA-256 hex digest of an action token for storage in PostgreSQL.

    Conforms to the ck_email_verification_tokens_token_hash constraint: ^[0-9a-f]{64}$
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

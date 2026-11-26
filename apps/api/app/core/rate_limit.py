"""Rate limiting and abuse prevention (T-08).

Provides Redis-backed, concurrency-safe atomic rate limiting with in-memory stricter
fallback, privacy-preserving key hashing, and trusted reverse proxy IP derivation.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import logging
import math
import time
from dataclasses import dataclass
from typing import Annotated, Any

import redis.asyncio as aioredis
from fastapi import Depends, Request, Response, status
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError

from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.schemas.auth import LoginRequest, ResendVerificationRequest

logger = logging.getLogger(__name__)

# Atomic Redis Lua script for fixed-window rate limiting with bounded TTL.
# Eliminates race conditions between separate GET, INCR, and EXPIRE commands.
# Returns {allowed (1 or 0), remaining (integer), retry_after (integer seconds)}.
ATOMIC_RATE_LIMIT_LUA = """
local key = KEYS[1]
local window = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])

local current = redis.call('INCR', key)
if current == 1 then
    redis.call('EXPIRE', key, window)
end

local ttl = redis.call('TTL', key)
if ttl < 0 then
    redis.call('EXPIRE', key, window)
    ttl = window
end

if current <= limit then
    return {1, limit - current, ttl}
else
    return {0, 0, math.max(1, ttl)}
end
"""


@dataclass(frozen=True)
class RateLimitRule:
    """Specification of rate limit thresholds and fallback limits."""

    name: str
    max_requests: int
    window_seconds: int
    fallback_max_requests: int
    fallback_window_seconds: int


@dataclass(frozen=True)
class RateLimitResult:
    """Result of a rate limit evaluation."""

    allowed: bool
    remaining: int
    retry_after: int
    is_fallback: bool
    limit: int


# =============================================================================
# Authoritative Limits from Feature Ticket List (T-08) & Security Document (2.6)
# =============================================================================

# Login: 5 attempts per 15 min per email; 20 attempts per 15 min per IP.
# Stricter fallback: 3 per 15 min per email; 10 per 15 min per IP.
RULE_LOGIN_EMAIL = RateLimitRule(
    name="login_email",
    max_requests=5,
    window_seconds=15 * 60,
    fallback_max_requests=3,
    fallback_window_seconds=15 * 60,
)

RULE_LOGIN_IP = RateLimitRule(
    name="login_ip",
    max_requests=20,
    window_seconds=15 * 60,
    fallback_max_requests=10,
    fallback_window_seconds=15 * 60,
)

# Signup: 5 attempts per hour per IP.
# Stricter fallback: 2 per hour per IP.
RULE_SIGNUP_IP = RateLimitRule(
    name="signup_ip",
    max_requests=5,
    window_seconds=60 * 60,
    fallback_max_requests=2,
    fallback_window_seconds=60 * 60,
)

# Anonymous CSRF bootstrap (GET /api/v1/auth/csrf): 60 per minute per IP.
# Stricter fallback: 20 per minute per IP.
RULE_CSRF_IP = RateLimitRule(
    name="csrf_ip",
    max_requests=60,
    window_seconds=60,
    fallback_max_requests=20,
    fallback_window_seconds=60,
)

# Password reset: 3 per hour per email (prepared for T-11).
RULE_PASSWORD_RESET_EMAIL = RateLimitRule(
    name="password_reset_email",
    max_requests=3,
    window_seconds=60 * 60,
    fallback_max_requests=2,
    fallback_window_seconds=60 * 60,
)

# Resend verification: 3 per hour (T-10).
RULE_RESEND_VERIFICATION_EMAIL = RateLimitRule(
    name="resend_verification_email",
    max_requests=3,
    window_seconds=60 * 60,
    fallback_max_requests=2,
    fallback_window_seconds=60 * 60,
)

# Resend verification per IP: 10 per hour (T-10).
RULE_RESEND_VERIFICATION_IP = RateLimitRule(
    name="resend_verification_ip",
    max_requests=10,
    window_seconds=60 * 60,
    fallback_max_requests=5,
    fallback_window_seconds=60 * 60,
)

# Visit requests: 10 per day per tenant (prepared for future ticket).
RULE_VISIT_REQUESTS = RateLimitRule(
    name="visit_requests",
    max_requests=10,
    window_seconds=24 * 60 * 60,
    fallback_max_requests=5,
    fallback_window_seconds=24 * 60 * 60,
)

# AI search: 20 per day per signed-in user, 5 per day per guest.
RULE_AI_SEARCH_USER = RateLimitRule(
    name="ai_search_user",
    max_requests=20,
    window_seconds=24 * 60 * 60,
    fallback_max_requests=10,
    fallback_window_seconds=24 * 60 * 60,
)

RULE_AI_SEARCH_GUEST = RateLimitRule(
    name="ai_search_guest",
    max_requests=5,
    window_seconds=24 * 60 * 60,
    fallback_max_requests=2,
    fallback_window_seconds=24 * 60 * 60,
)


# =============================================================================
# Trusted Proxy and Client IP Extraction
# =============================================================================


def is_ip_in_trusted_proxies(ip_str: str, trusted_proxies: list[str]) -> bool:
    """Check if an IP string matches the trusted proxies configuration.

    Supports exact string match, individual IP addresses, and CIDR subnets.
    """
    clean_ip = ip_str.strip().lower()
    for entry in trusted_proxies:
        entry_clean = entry.strip().lower()
        if not entry_clean:
            continue
        if clean_ip == entry_clean:
            return True
        try:
            target_ip = ipaddress.ip_address(clean_ip)
            if "/" in entry_clean:
                network = ipaddress.ip_network(entry_clean, strict=False)
                if target_ip in network:
                    return True
            else:
                trusted_ip = ipaddress.ip_address(entry_clean)
                if target_ip == trusted_ip:
                    return True
        except ValueError:
            # Non-IP string comparison already attempted
            continue
    return False


def get_client_ip(request: Request, settings: Settings) -> str:
    """Derive effective client IP address enforcing strict reverse-proxy trust rules.

    Invariants:
    1. If the direct remote host (request.client.host) is NOT a trusted proxy,
       all client-supplied forwarded headers (X-Forwarded-For, X-Real-IP, Forwarded)
       are completely IGNORED to prevent header spoofing.
    2. If the direct remote host IS a trusted proxy, X-Forwarded-For is parsed from
       right to left. The first untrusted IP in the chain is returned as the client IP.
    3. Handles IPv4, IPv6, missing client, and malformed header inputs safely.
    """
    if request.client is None or not request.client.host:
        return "127.0.0.1"

    peer_ip = request.client.host.strip()

    # Reject/ignore forwarding headers if immediate peer is not trusted
    if not is_ip_in_trusted_proxies(peer_ip, settings.trusted_proxies):
        return peer_ip

    # Peer is trusted: inspect X-Forwarded-For chain from right to left
    xff_header = request.headers.get("X-Forwarded-For")
    if xff_header:
        raw_ips = [p.strip() for p in xff_header.split(",") if p.strip()]
        for candidate in reversed(raw_ips):
            try:
                # Validate syntactic IP correctness
                ipaddress.ip_address(candidate)
            except ValueError:
                # Malformed entry in forwarding chain: ignore invalid IP token
                continue

            if not is_ip_in_trusted_proxies(candidate, settings.trusted_proxies):
                return candidate

        # If all valid IPs in the chain were trusted proxies, return leftmost valid IP
        for candidate in raw_ips:
            try:
                ipaddress.ip_address(candidate)
                return candidate
            except ValueError:
                continue

    # Fall back to X-Real-IP if peer is trusted and XFF had no untrusted client
    x_real_ip = request.headers.get("X-Real-IP")
    if x_real_ip:
        candidate_real = x_real_ip.strip()
        try:
            ipaddress.ip_address(candidate_real)
            return candidate_real
        except ValueError:
            pass

    return peer_ip


# =============================================================================
# Privacy-Preserving Key Hashing
# =============================================================================


def hash_identifier(raw_identifier: str) -> str:
    """Hash identifiers with SHA-256 to ensure no plain emails, IPs, or tokens reach Redis.

    Returns the first 32 hex characters (128 bits) of the lowercase digest.
    """
    normalized = raw_identifier.strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]


def make_rate_limit_key(namespace: str, scope: str, raw_identifier: str) -> str:
    """Construct safe, namespaced Redis rate-limit key with hashed identifier."""
    hashed = hash_identifier(raw_identifier)
    return f"rl:{namespace}:{scope}:{hashed}"


# =============================================================================
# In-Memory Stricter Fallback Rate Limiter
# =============================================================================


class InMemoryRateLimiter:
    """Thread-safe and asyncio-safe in-memory rate limiter for fallback when Redis is down.

    Uses a sliding-window timestamp list with automatic TTL cleanup to prevent
    unbounded memory growth.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._records: dict[str, list[float]] = {}
        self._last_cleanup: float = time.monotonic()

    async def check(
        self, key: str, max_requests: int, window_seconds: int
    ) -> tuple[bool, int, int]:
        """Evaluate request against in-memory sliding window.

        Returns (allowed, remaining, retry_after).
        """
        async with self._lock:
            now = time.monotonic()

            # Periodic cleanup every 60 seconds
            if now - self._last_cleanup > 60:
                self._cleanup_expired(now)

            timestamps = self._records.get(key, [])
            cutoff = now - window_seconds
            valid_timestamps = [ts for ts in timestamps if ts > cutoff]

            if len(valid_timestamps) < max_requests:
                valid_timestamps.append(now)
                self._records[key] = valid_timestamps
                remaining = max_requests - len(valid_timestamps)
                # Next expiration is for the oldest timestamp in the active window
                retry_after = max(1, math.ceil(valid_timestamps[0] + window_seconds - now))
                return True, remaining, retry_after
            else:
                self._records[key] = valid_timestamps
                oldest = valid_timestamps[0]
                retry_after = max(1, math.ceil(oldest + window_seconds - now))
                return False, 0, retry_after

    def _cleanup_expired(self, now: float) -> None:
        """Remove empty or completely expired keys to prevent memory leaks."""
        self._last_cleanup = now
        stale_keys = []
        for k, timestamps in self._records.items():
            # If newest timestamp is older than 24 hours, prune key
            if not timestamps or (now - timestamps[-1] > 86400):
                stale_keys.append(k)
        for k in stale_keys:
            self._records.pop(k, None)

    async def clear(self) -> None:
        """Clear all in-memory rate limit records (test fixture helper)."""
        async with self._lock:
            self._records.clear()


# Global in-memory fallback instance
_in_memory_limiter = InMemoryRateLimiter()

# =============================================================================
# Redis Client Management
# =============================================================================

_redis_client: aioredis.Redis | None = None
_redis_client_override: Any | None = None


def set_redis_client_override(client: Any | None) -> None:
    """Inject a mock or explicit Redis client for testing."""
    global _redis_client_override
    _redis_client_override = client


def get_redis_client(settings: Settings) -> Any | None:
    """Retrieve or create an async Redis connection client."""
    global _redis_client
    if _redis_client_override is not None:
        return _redis_client_override

    if not settings.redis_url:
        return None

    if _redis_client is None:
        _redis_client = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=settings.rate_limit_connect_timeout_seconds,
            socket_timeout=settings.rate_limit_connect_timeout_seconds,
        )
    return _redis_client


async def close_redis_client() -> None:
    """Cleanly close the Redis connection pool."""
    global _redis_client
    if _redis_client is not None:
        try:
            await _redis_client.aclose()
        except Exception:
            pass
        _redis_client = None


async def reset_rate_limit_state() -> None:
    """Reset both in-memory and Redis test state."""
    await _in_memory_limiter.clear()
    if _redis_client_override is not None:
        try:
            await _redis_client_override.flushdb()
        except Exception:
            pass


# =============================================================================
# Core Rate Limit Evaluation Engine
# =============================================================================


async def evaluate_rate_limit(
    key: str,
    rule: RateLimitRule,
    settings: Settings,
) -> RateLimitResult:
    """Evaluate rate limit using Redis atomic Lua script with fail-closed in-memory fallback.

    Invariants:
    1. If rate_limit_enabled is False, allows the request with maximum remaining.
    2. Attempts Redis evaluation via ATOMIC_RATE_LIMIT_LUA.
    3. If Redis is unconfigured or unreachable (timeout/connection failure), logs a
       warning and falls back to InMemoryRateLimiter with STRICTER limits.
    4. Rate limits are NEVER silently bypassed.
    """
    if not settings.rate_limit_enabled:
        return RateLimitResult(
            allowed=True,
            remaining=rule.max_requests,
            retry_after=0,
            is_fallback=False,
            limit=rule.max_requests,
        )

    redis_conn = get_redis_client(settings)

    if redis_conn is not None:
        try:
            # Atomic Lua script execution: returns [allowed, remaining, ttl]
            res = await asyncio.wait_for(
                redis_conn.eval(
                    ATOMIC_RATE_LIMIT_LUA,
                    1,
                    key,
                    rule.window_seconds,
                    rule.max_requests,
                ),
                timeout=settings.rate_limit_connect_timeout_seconds,
            )
            allowed = bool(res[0])
            remaining = int(res[1])
            retry_after = int(res[2])
            return RateLimitResult(
                allowed=allowed,
                remaining=remaining,
                retry_after=retry_after,
                is_fallback=False,
                limit=rule.max_requests,
            )
        except (RedisConnectionError, RedisTimeoutError, TimeoutError, RedisError) as exc:
            logger.warning(
                "Redis rate limiter unavailable (%s: %s); "
                "falling back to stricter in-memory limits.",
                type(exc).__name__,
                str(exc),
                extra={"rule": rule.name, "key_prefix": key[:20]},
            )
        except Exception as exc:
            logger.warning(
                "Unexpected Redis error (%s: %s); falling back to stricter in-memory limits.",
                type(exc).__name__,
                str(exc),
                extra={"rule": rule.name, "key_prefix": key[:20]},
            )

    # In-memory stricter fallback
    allowed, remaining, retry_after = await _in_memory_limiter.check(
        key=key,
        max_requests=rule.fallback_max_requests,
        window_seconds=rule.fallback_window_seconds,
    )
    return RateLimitResult(
        allowed=allowed,
        remaining=remaining,
        retry_after=retry_after,
        is_fallback=True,
        limit=rule.fallback_max_requests,
    )


def enforce_rate_limit(result: RateLimitResult) -> None:
    """Raise HTTP 429 AppError if rate limit is exceeded, attaching Retry-After headers."""
    if not result.allowed:
        raise AppError(
            code="RATE_LIMIT_EXCEEDED",
            message="Too many requests. Please try again later.",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            headers={
                "Retry-After": str(max(1, result.retry_after)),
                "X-RateLimit-Limit": str(result.limit),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(max(1, result.retry_after)),
            },
        )


def set_rate_limit_headers(response: Response, result: RateLimitResult) -> None:
    """Attach standard informational rate limit headers to successful responses."""
    response.headers["X-RateLimit-Limit"] = str(result.limit)
    response.headers["X-RateLimit-Remaining"] = str(result.remaining)
    response.headers["X-RateLimit-Reset"] = str(max(0, result.retry_after))


# =============================================================================
# FastAPI Endpoint Dependencies
# =============================================================================


async def rate_limit_csrf(
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """Rate limit anonymous CSRF bootstrap (GET /api/v1/auth/csrf) by client IP."""
    client_ip = get_client_ip(request, settings)
    key = make_rate_limit_key("csrf", "ip", client_ip)
    result = await evaluate_rate_limit(key, RULE_CSRF_IP, settings)
    enforce_rate_limit(result)
    set_rate_limit_headers(response, result)


async def rate_limit_signup(
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """Rate limit account registration (POST /api/v1/auth/signup) by client IP."""
    client_ip = get_client_ip(request, settings)
    key = make_rate_limit_key("signup", "ip", client_ip)
    result = await evaluate_rate_limit(key, RULE_SIGNUP_IP, settings)
    enforce_rate_limit(result)
    set_rate_limit_headers(response, result)


async def rate_limit_login(
    request: Request,
    response: Response,
    payload: LoginRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """Rate limit authentication (POST /api/v1/auth/login) by both IP and normalized account email.

    Dual-limit policy:
    1. Per-IP: 20 attempts per 15 min (fallback 10)
    2. Per-Email: 5 attempts per 15 min (fallback 3)
    Both limits are evaluated. If either limit is exceeded, HTTP 429 is returned.
    """
    client_ip = get_client_ip(request, settings)
    ip_key = make_rate_limit_key("login", "ip", client_ip)
    email_key = make_rate_limit_key("login", "email", payload.email)

    ip_result = await evaluate_rate_limit(ip_key, RULE_LOGIN_IP, settings)
    email_result = await evaluate_rate_limit(email_key, RULE_LOGIN_EMAIL, settings)

    # If either limit exceeded, enforce the one that failed (or the stricter retry_after)
    if not ip_result.allowed:
        enforce_rate_limit(ip_result)
    if not email_result.allowed:
        enforce_rate_limit(email_result)

    # For successful requests, headers reflect the stricter (lower remaining) counter
    if email_result.remaining < ip_result.remaining:
        set_rate_limit_headers(response, email_result)
    else:
        set_rate_limit_headers(response, ip_result)


async def rate_limit_resend_verification(
    request: Request,
    response: Response,
    payload: ResendVerificationRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """Rate limit resend verification requests by both client IP and account email.

    Dual-limit policy:
    1. Per-IP: 10 attempts per hour (fallback 5) to prevent mass spam.
    2. Per-Email: 3 attempts per hour (fallback 2) to prevent user harassment.
    """
    if not settings.rate_limit_enabled:
        return

    client_ip = get_client_ip(request, settings)

    # 1. Per-IP evaluation
    ip_key = make_rate_limit_key("resend_verification", "ip", client_ip)
    ip_result = await evaluate_rate_limit(ip_key, RULE_RESEND_VERIFICATION_IP, settings)

    # 2. Per-Email evaluation
    normalized_email = payload.email.strip().lower()
    email_key = make_rate_limit_key("resend_verification", "email", normalized_email)
    email_result = await evaluate_rate_limit(email_key, RULE_RESEND_VERIFICATION_EMAIL, settings)

    # If either limit exceeded, enforce the one that failed
    if not ip_result.allowed:
        enforce_rate_limit(ip_result)
    if not email_result.allowed:
        enforce_rate_limit(email_result)

    # For successful requests, headers reflect the stricter (lower remaining) counter
    if email_result.remaining < ip_result.remaining:
        set_rate_limit_headers(response, email_result)
    else:
        set_rate_limit_headers(response, ip_result)

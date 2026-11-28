"""Provider boundary. Wired to no release entry point until independently verified."""

import ssl
from typing import Never

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.migration_guard import (
    APPROVED_STAGING_BRANCH_ID,
    APPROVED_STAGING_DATABASE,
    APPROVED_STAGING_ENDPOINT_ID,
    APPROVED_STAGING_HOST,
    APPROVED_STAGING_MIGRATION_ROLE,
    APPROVED_STAGING_PROJECT_ID,
    _match_cert_hostname,
    verify_neon_control_plane,
)


async def verify_staging_session(connection: AsyncConnection) -> Never:
    # This function is deliberately unreachable from the disabled staging entry point.
    metadata = verify_neon_control_plane(require_read_write=True)
    if (metadata.project_id, metadata.branch_id, metadata.endpoint_id, metadata.host) != (
        APPROVED_STAGING_PROJECT_ID,
        APPROVED_STAGING_BRANCH_ID,
        APPROVED_STAGING_ENDPOINT_ID,
        APPROVED_STAGING_HOST,
    ):
        raise RuntimeError("Control-plane target mismatch")
    raw = await connection.get_raw_connection()
    transport = getattr(raw.driver_connection, "_transport", None)
    peer = transport.get_extra_info("ssl_object") if transport is not None else None
    if (
        peer is None
        or peer.context.verify_mode != ssl.CERT_REQUIRED
        or not peer.context.check_hostname
        or peer.server_hostname != metadata.host
    ):
        raise RuntimeError("Unverified TLS peer")
    certificate = peer.getpeercert()
    if not certificate:
        raise RuntimeError("Missing authenticated TLS certificate")
    _match_cert_hostname(certificate, APPROVED_STAGING_HOST)
    users = await connection.execute(
        text("SELECT pg_catalog.current_database(),current_user,session_user")
    )
    if tuple(users.one()) != (
        APPROVED_STAGING_DATABASE,
        APPROVED_STAGING_MIGRATION_ROLE,
        APPROVED_STAGING_MIGRATION_ROLE,
    ):
        raise RuntimeError("Migration principal or database mismatch")
    raise RuntimeError("Provider-issued session branch and endpoint binding unavailable")

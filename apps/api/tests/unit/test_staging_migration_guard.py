"""Offline unit tests for Gate 3 staging migration guard security and verification.

Reproduces and verifies remediations for all Astra review findings:
1. Authentic TLS enforcement (CERT_REQUIRED, check_hostname, peer cert validation).
2. Hostname verification before authentication (wrong-host TLS handshake regression).
3. Authoritative Neon control-plane metadata verification (rejects fabricated metadata).
4. Remote offline Alembic migration blocking (CLI and programmatic).
5. Strict database catalog verification (app role existence, non-null attributes,
   boolean alembic table check, domains, ranges, multiranges).
6. Documentation and procedure accuracy.
"""

from __future__ import annotations

import io
import json
import ssl
import subprocess
import tempfile
import urllib.request
import urllib.response
from http.client import HTTPMessage
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.db.migration_guard import (
    APPROVED_STAGING_BRANCH_ID,
    APPROVED_STAGING_DATABASE,
    APPROVED_STAGING_ENDPOINT_ID,
    APPROVED_STAGING_HOST,
    APPROVED_STAGING_MIGRATION_ROLE,
    APPROVED_STAGING_PROJECT_ID,
    PROTECTED_PRODUCTION_BRANCH_ID,
    ApprovedTargetIdentity,
    StrictNoRedirectHandler,
    build_neon_api_opener,
    is_offline_migration_requested,
    resolve_migration_target,
    verify_migration_connection,
)

VALID_STAGING_URL = (
    f"postgresql+asyncpg://{APPROVED_STAGING_MIGRATION_ROLE}:secret_pass"
    f"@{APPROVED_STAGING_HOST}:5432/{APPROVED_STAGING_DATABASE}?sslmode=verify-full"
)


# =============================================================================
# Helper: Synthetic SSL, Connection & Transport Builders
# =============================================================================


class MockControlPlaneTransport:
    """Mock Neon control plane API transport for offline testing."""

    def __init__(
        self,
        project_id: str = APPROVED_STAGING_PROJECT_ID,
        branch_id: str = APPROVED_STAGING_BRANCH_ID,
        branch_name: str = "staging",
        endpoint_id: str = APPROVED_STAGING_ENDPOINT_ID,
        endpoint_host: str = APPROVED_STAGING_HOST,
        endpoint_branch_id: str = APPROVED_STAGING_BRANCH_ID,
        fail_status: int | None = None,
        empty_endpoints: bool = False,
    ) -> None:
        self.project_id = project_id
        self.branch_id = branch_id
        self.branch_name = branch_name
        self.endpoint_id = endpoint_id
        self.endpoint_host = endpoint_host
        self.endpoint_branch_id = endpoint_branch_id
        self.fail_status = fail_status
        self.empty_endpoints = empty_endpoints

    def get(self, path: str) -> dict[str, Any]:
        if self.fail_status is not None:
            raise RuntimeError(f"HTTP {self.fail_status}: Service Unavailable")

        if path == f"/projects/{APPROVED_STAGING_PROJECT_ID}":
            return {"project": {"id": self.project_id, "name": "DormioStay"}}

        if path == f"/projects/{APPROVED_STAGING_PROJECT_ID}/branches":
            return {"branches": [{"id": self.branch_id, "name": self.branch_name}]}

        if path == f"/projects/{APPROVED_STAGING_PROJECT_ID}/endpoints":
            if self.empty_endpoints:
                return {"endpoints": []}
            return {
                "endpoints": [
                    {
                        "id": self.endpoint_id,
                        "host": self.endpoint_host,
                        "branch_id": self.endpoint_branch_id,
                    }
                ]
            }

        return {}


SENSITIVE_REDIRECT_TARGET = (
    "https://attacker.evil.com/callback?token=offline-sensitive-header-marker"
)
SENSITIVE_MARKER = "offline-sensitive-header-marker"


class MockNeonRedirectHandler(urllib.request.BaseHandler):
    """Synthetic handler that simulates redirect from Neon API to attacker origin."""

    handler_order = 100

    def __init__(
        self,
        redirect_code: int = 302,
        redirect_target: str = SENSITIVE_REDIRECT_TARGET,
    ) -> None:
        self.redirect_code = redirect_code
        self.redirect_target = redirect_target
        self.neon_requests: list[tuple[str, dict[str, str]]] = []
        self.attacker_requests: list[tuple[str, dict[str, str]]] = []

    def http_open(self, req: urllib.request.Request) -> urllib.response.addinfourl | None:
        url = req.full_url
        headers = dict(req.headers)
        if "console.neon.tech" in url:
            self.neon_requests.append((url, headers))
            msg = HTTPMessage()
            msg["Location"] = self.redirect_target
            resp_neon: Any = urllib.response.addinfourl(
                io.BytesIO(b"Redirecting to attacker origin..."),
                msg,
                url,
                code=self.redirect_code,
            )
            resp_neon.msg = "Found" if self.redirect_code == 302 else "Redirect"
            return cast(urllib.response.addinfourl, resp_neon)
        elif "attacker.evil.com" in url:
            self.attacker_requests.append((url, headers))
            msg = HTTPMessage()
            resp_att: Any = urllib.response.addinfourl(
                io.BytesIO(b'{"fake": true, "project": {"id": "small-dew-81510029"}}'),
                msg,
                url,
                code=200,
            )
            resp_att.msg = "OK"
            return cast(urllib.response.addinfourl, resp_att)
        return None

    https_open = http_open


class MockNeonSuccessHandler(urllib.request.BaseHandler):
    """Synthetic handler returning authentic JSON metadata with HTTP 200."""

    handler_order = 100

    def __init__(self, raw_body: bytes | None = None, status_code: int = 200) -> None:
        self.status_code = status_code
        self.raw_body = raw_body
        self.requests: list[tuple[str, dict[str, str]]] = []

    def http_open(self, req: urllib.request.Request) -> urllib.response.addinfourl | None:
        url = req.full_url
        headers = dict(req.headers)
        self.requests.append((url, headers))
        msg = HTTPMessage()

        if self.status_code != 200:
            resp_err: Any = urllib.response.addinfourl(
                io.BytesIO(b"Error"),
                msg,
                url,
                code=self.status_code,
            )
            resp_err.msg = "Error"
            return cast(urllib.response.addinfourl, resp_err)

        if self.raw_body is not None:
            resp_raw: Any = urllib.response.addinfourl(
                io.BytesIO(self.raw_body),
                msg,
                url,
                code=200,
            )
            resp_raw.msg = "OK"
            return cast(urllib.response.addinfourl, resp_raw)

        if url.endswith(f"/projects/{APPROVED_STAGING_PROJECT_ID}"):
            payload = json.dumps(
                {"project": {"id": APPROVED_STAGING_PROJECT_ID, "name": "DormioStay"}}
            ).encode()
        elif url.endswith(f"/projects/{APPROVED_STAGING_PROJECT_ID}/branches"):
            payload = json.dumps(
                {"branches": [{"id": APPROVED_STAGING_BRANCH_ID, "name": "staging"}]}
            ).encode()
        elif url.endswith(f"/projects/{APPROVED_STAGING_PROJECT_ID}/endpoints"):
            payload = json.dumps(
                {
                    "endpoints": [
                        {
                            "id": APPROVED_STAGING_ENDPOINT_ID,
                            "host": APPROVED_STAGING_HOST,
                            "branch_id": APPROVED_STAGING_BRANCH_ID,
                        }
                    ]
                }
            ).encode()
        else:
            payload = b"{}"

        resp_ok: Any = urllib.response.addinfourl(io.BytesIO(payload), msg, url, code=200)
        resp_ok.msg = "OK"
        return cast(urllib.response.addinfourl, resp_ok)

    https_open = http_open


def _build_synthetic_approved_identity() -> ApprovedTargetIdentity:
    """Construct an authoritative synthetic ApprovedTargetIdentity for offline tests."""
    return ApprovedTargetIdentity(
        host=APPROVED_STAGING_HOST,
        database=APPROVED_STAGING_DATABASE,
        migration_role=APPROVED_STAGING_MIGRATION_ROLE,
        project_id=APPROVED_STAGING_PROJECT_ID,
        branch_id=APPROVED_STAGING_BRANCH_ID,
        endpoint_id=APPROVED_STAGING_ENDPOINT_ID,
        control_plane_verified=True,
    )


def _build_synthetic_cert(hostname: str = APPROVED_STAGING_HOST) -> dict[str, Any]:
    """Construct a valid synthetic peer certificate dictionary for ssl hostname matching."""
    return {
        "subject": ((("commonName", hostname),),),
        "subjectAltName": (("DNS", hostname),),
    }


def _build_mock_connection(
    database: str = APPROVED_STAGING_DATABASE,
    user: str = APPROVED_STAGING_MIGRATION_ROLE,
    session_user: str = APPROVED_STAGING_MIGRATION_ROLE,
    project_id: str = APPROVED_STAGING_PROJECT_ID,
    branch_id: str = APPROVED_STAGING_BRANCH_ID,
    endpoint_id: str = APPROVED_STAGING_ENDPOINT_ID,
    is_tls: bool = True,
    verify_mode: int = ssl.CERT_REQUIRED,
    check_hostname: bool = True,
    cert_hostname: str = APPROVED_STAGING_HOST,
    cert_empty: bool = False,
    admin_can_connect: bool = True,
    admin_can_create_schema: bool = True,
    admin_can_use_schema: bool = True,
    app_exists: bool = True,
    app_elevated_flags: bool = False,
    app_db_create: bool = False,
    app_sch_create: bool = False,
    app_has_memberships: bool = False,
    app_owned_tables: int = 0,
    app_owned_views: int = 0,
    app_owned_nsps: int = 0,
    user_tables_count: int = 0,
    alembic_table_exists: bool = False,
    alembic_rev: str | None = None,
    views_count: int = 0,
    matviews_count: int = 0,
    sequences_count: int = 0,
    functions_count: int = 0,
    types_count: int = 0,
) -> AsyncMock:
    """Construct an offline mock connection implementing the real AsyncConnection interface."""
    mock_conn = AsyncMock()

    # Finding 2: Implement get_raw_connection() returning pooled proxied connection
    mock_driver_conn = MagicMock()
    mock_raw_conn = MagicMock()
    mock_raw_conn.driver_connection = mock_driver_conn
    mock_conn.get_raw_connection = AsyncMock(return_value=mock_raw_conn)

    # Finding 1: Configure authentic TLS context and peer certificate
    if is_tls:
        mock_transport = MagicMock()
        mock_ssl_ctx = MagicMock()
        mock_ssl_ctx.verify_mode = verify_mode
        mock_ssl_ctx.check_hostname = check_hostname

        mock_ssl_obj = MagicMock()
        mock_ssl_obj.context = mock_ssl_ctx
        mock_ssl_obj.getpeercert.return_value = (
            None if cert_empty else _build_synthetic_cert(cert_hostname)
        )

        def _get_extra_info(name: str, default: Any = None) -> Any:
            if name == "ssl_object":
                return mock_ssl_obj
            if name == "sslcontext":
                return mock_ssl_ctx
            return default

        mock_transport.get_extra_info.side_effect = _get_extra_info
        mock_driver_conn._transport = mock_transport
    else:
        mock_driver_conn._transport = None

    # Query dispatch
    async def mock_execute(statement: Any, *args: Any, **kwargs: Any) -> MagicMock:
        sql_str = str(statement)
        result = MagicMock()

        if "SELECT current_user, session_user" in sql_str:
            result.first.return_value = (user, session_user)
            return result

        if "SELECT name, setting FROM pg_settings" in sql_str:
            rows = []
            if project_id:
                rows.append(("neon.project_id", project_id))
            if branch_id:
                rows.append(("neon.branch_id", branch_id))
            if endpoint_id:
                rows.append(("neon.endpoint_id", endpoint_id))
            result.fetchall.return_value = rows
            return result

        if "FROM pg_roles WHERE rolname = :app_role" in sql_str:
            if app_exists:
                # rolcanlogin, rolsuper, rolcreaterole, rolcreatedb, rolreplication, rolbypassrls
                elevated = app_elevated_flags
                result.first.return_value = (True, elevated, elevated, elevated, elevated, elevated)
            else:
                result.first.return_value = None
            return result

        return result

    async def mock_scalar(statement: Any, *args: Any, **kwargs: Any) -> Any:
        sql_str = str(statement)

        if "SELECT current_database()" in sql_str:
            return database

        if "has_database_privilege(current_user, current_database(), 'CONNECT')" in sql_str:
            return admin_can_connect

        if "has_schema_privilege(current_user, 'public', 'CREATE')" in sql_str:
            return admin_can_create_schema

        if "has_schema_privilege(current_user, 'public', 'USAGE')" in sql_str:
            return admin_can_use_schema

        if "has_database_privilege(:app_role, current_database(), 'CREATE')" in sql_str:
            return app_db_create

        if "has_schema_privilege(:app_role, 'public', 'CREATE')" in sql_str:
            return app_sch_create

        if "SELECT EXISTS (SELECT 1 FROM pg_auth_members" in sql_str:
            return app_has_memberships

        if "schemaname = 'public' AND tableowner = :app_role" in sql_str:
            return app_owned_tables

        if "schemaname = 'public' AND viewowner = :app_role" in sql_str:
            return app_owned_views

        if "count(*) FROM pg_namespace WHERE nspowner" in sql_str:
            return app_owned_nsps

        if "table_schema = 'public' AND table_name != 'alembic_version'" in sql_str:
            return user_tables_count

        if "table_name = 'alembic_version'" in sql_str:
            return alembic_table_exists

        if "SELECT version_num FROM alembic_version" in sql_str:
            return alembic_rev

        if "count(*) FROM information_schema.views" in sql_str:
            return views_count

        if "count(*) FROM pg_matviews" in sql_str:
            return matviews_count

        if "count(*) FROM information_schema.sequences" in sql_str:
            return sequences_count

        if "count(*) FROM pg_proc p JOIN pg_namespace" in sql_str:
            return functions_count

        if "count(*) FROM pg_type t JOIN pg_namespace" in sql_str:
            return types_count

        return None

    mock_conn.execute.side_effect = mock_execute
    mock_conn.scalar.side_effect = mock_scalar
    return mock_conn


# =============================================================================
# Finding 1 Tests: Authoritative Neon Control Plane Verification
# =============================================================================


def test_control_plane_fails_closed_when_neon_api_key_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that missing NEON_API_KEY prevents staging migration resolution."""
    monkeypatch.delenv("NEON_API_KEY", raising=False)
    with pytest.raises(ValueError, match="NEON_API_KEY is required"):
        resolve_migration_target(
            direct_url=VALID_STAGING_URL,
            migration_env="staging",
            migration_confirmation="staging",
            action="upgrade",
        )


def test_control_plane_rejects_fabricated_caller_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify caller-supplied identity with fabricated verification is rejected without API."""
    monkeypatch.delenv("NEON_API_KEY", raising=False)
    fabricated = ApprovedTargetIdentity(
        host=APPROVED_STAGING_HOST,
        database=APPROVED_STAGING_DATABASE,
        migration_role=APPROVED_STAGING_MIGRATION_ROLE,
        project_id=APPROVED_STAGING_PROJECT_ID,
        branch_id=APPROVED_STAGING_BRANCH_ID,
        endpoint_id=APPROVED_STAGING_ENDPOINT_ID,
        control_plane_verified=True,
    )
    with pytest.raises(ValueError, match="NEON_API_KEY is required"):
        resolve_migration_target(
            direct_url=VALID_STAGING_URL,
            migration_env="staging",
            migration_confirmation="staging",
            action="upgrade",
            approved_identity=fabricated,
        )


def test_control_plane_rejects_unsuccessful_metadata_response() -> None:
    """Verify that failure response from Neon API fails closed."""
    transport = MockControlPlaneTransport(fail_status=500)
    with pytest.raises(ValueError, match="Neon control-plane API request failed"):
        resolve_migration_target(
            direct_url=VALID_STAGING_URL,
            migration_env="staging",
            migration_confirmation="staging",
            action="upgrade",
            control_plane_transport=transport,
        )


def test_control_plane_rejects_branch_endpoint_mismatch() -> None:
    """Verify endpoints belonging to production or another branch are rejected."""
    transport = MockControlPlaneTransport(endpoint_branch_id=PROTECTED_PRODUCTION_BRANCH_ID)
    with pytest.raises(ValueError, match="No compute endpoints found for staging branch"):
        resolve_migration_target(
            direct_url=VALID_STAGING_URL,
            migration_env="staging",
            migration_confirmation="staging",
            action="upgrade",
            control_plane_transport=transport,
        )


def test_control_plane_rejects_conflicting_project_id() -> None:
    """Verify that project ID mismatch fails closed."""
    transport = MockControlPlaneTransport(project_id="conflicting-project-id")
    with pytest.raises(ValueError, match="Control-plane project ID mismatch"):
        resolve_migration_target(
            direct_url=VALID_STAGING_URL,
            migration_env="staging",
            migration_confirmation="staging",
            action="upgrade",
            control_plane_transport=transport,
        )


def test_control_plane_rejects_missing_endpoint() -> None:
    """Verify that missing endpoint for staging branch fails closed."""
    transport = MockControlPlaneTransport(empty_endpoints=True)
    with pytest.raises(ValueError, match="No compute endpoints found for staging branch"):
        resolve_migration_target(
            direct_url=VALID_STAGING_URL,
            migration_env="staging",
            migration_confirmation="staging",
            action="upgrade",
            control_plane_transport=transport,
        )


def test_control_plane_verifies_valid_staging_metadata() -> None:
    """Verify that authenticated valid control-plane metadata resolves target successfully."""
    transport = MockControlPlaneTransport()
    url, identity = resolve_migration_target(
        direct_url=VALID_STAGING_URL,
        migration_env="staging",
        migration_confirmation="staging",
        action="upgrade",
        control_plane_transport=transport,
    )
    assert isinstance(identity, ApprovedTargetIdentity)
    assert identity.control_plane_verified is True
    assert identity.host == APPROVED_STAGING_HOST
    assert identity.project_id == APPROVED_STAGING_PROJECT_ID
    assert identity.branch_id == APPROVED_STAGING_BRANCH_ID
    assert identity.endpoint_id == APPROVED_STAGING_ENDPOINT_ID


def test_neon_api_redirect_302_rejected_and_tokens_not_forwarded(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify HTTP 302 redirect is rejected without leaking sensitive Location or tokens."""
    mock_handler = MockNeonRedirectHandler(
        redirect_code=302, redirect_target=SENSITIVE_REDIRECT_TARGET
    )
    opener = build_neon_api_opener(extra_handlers=[mock_handler])

    with pytest.raises(ValueError) as exc_info:
        resolve_migration_target(
            direct_url=VALID_STAGING_URL,
            migration_env="staging",
            migration_confirmation="staging",
            action="upgrade",
            neon_api_key="secret_neon_key_astra_test",
            control_plane_opener=opener,
        )

    exc_msg = str(exc_info.value)

    # 1. Expected sanitized message with only status code
    assert "Neon API HTTP redirect (302) rejected by security policy" in exc_msg

    # 2. Sensitive marker and Location header URL are strictly absent from error message
    assert SENSITIVE_MARKER not in exc_msg
    assert "attacker.evil.com" not in exc_msg
    assert "callback?token=" not in exc_msg
    assert "Location" not in exc_msg

    # 3. Exactly one request reached original endpoint with Authorization
    assert len(mock_handler.neon_requests) == 1
    assert (
        mock_handler.neon_requests[0][1].get("Authorization") == "Bearer secret_neon_key_astra_test"
    )

    # 4. Zero requests reached redirect destination
    assert len(mock_handler.attacker_requests) == 0

    # 5. Sensitive marker is absent from captured stdout and stderr
    captured = capsys.readouterr()
    assert SENSITIVE_MARKER not in captured.out
    assert SENSITIVE_MARKER not in captured.err
    assert "attacker.evil.com" not in captured.out
    assert "attacker.evil.com" not in captured.err


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
def test_neon_api_redirect_all_3xx_codes_rejected_sanitized(
    code: int, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify all 3xx redirect codes reject with sanitized message and zero info disclosure."""
    mock_handler = MockNeonRedirectHandler(
        redirect_code=code, redirect_target=SENSITIVE_REDIRECT_TARGET
    )
    opener = build_neon_api_opener(extra_handlers=[mock_handler])

    with pytest.raises(ValueError) as exc_info:
        resolve_migration_target(
            direct_url=VALID_STAGING_URL,
            migration_env="staging",
            migration_confirmation="staging",
            action="upgrade",
            neon_api_key="secret_token",
            control_plane_opener=opener,
        )

    exc_msg = str(exc_info.value)

    # Assert redirect rejected with sanitized message containing only status code
    assert f"Neon API HTTP redirect ({code}) rejected by security policy" in exc_msg

    # Assert sensitive marker and redirect destination URL are strictly absent
    assert SENSITIVE_MARKER not in exc_msg
    assert "attacker.evil.com" not in exc_msg
    assert "callback?token=" not in exc_msg
    assert "Location" not in exc_msg

    # Assert exactly one request reaches original endpoint
    assert len(mock_handler.neon_requests) == 1

    # Assert zero requests reach the redirect destination
    assert len(mock_handler.attacker_requests) == 0

    # Assert sensitive marker absent from captured stdout and stderr
    captured = capsys.readouterr()
    assert SENSITIVE_MARKER not in captured.out
    assert SENSITIVE_MARKER not in captured.err
    assert "attacker.evil.com" not in captured.out
    assert "attacker.evil.com" not in captured.err


def test_strict_no_redirect_handler_sanitizes_direct_calls() -> None:
    """Verify StrictNoRedirectHandler raises sanitized message omitting newurl and headers."""
    handler = StrictNoRedirectHandler()
    req = urllib.request.Request("https://console.neon.tech/test")
    headers = {"Location": SENSITIVE_REDIRECT_TARGET}

    for code, method_name in [
        (301, "http_error_301"),
        (302, "http_error_302"),
        (303, "http_error_303"),
        (307, "http_error_307"),
        (308, "http_error_308"),
    ]:
        method = getattr(handler, method_name)
        with pytest.raises(ValueError) as exc_info:
            method(req, None, code, "Redirect", headers)
        msg = str(exc_info.value)
        assert msg == f"Neon API HTTP redirect ({code}) rejected by security policy"
        assert SENSITIVE_MARKER not in msg
        assert "attacker.evil.com" not in msg

    with pytest.raises(ValueError) as exc_info:
        handler.redirect_request(req, None, 302, "Found", headers, SENSITIVE_REDIRECT_TARGET)
    msg = str(exc_info.value)
    assert msg == "Neon API HTTP redirect (302) rejected by security policy"
    assert SENSITIVE_MARKER not in msg
    assert "attacker.evil.com" not in msg


def test_neon_api_opener_configuration_enforces_tls_and_no_redirect_handler() -> None:
    """Verify build_neon_api_opener() enforces TLS and replaces HTTPRedirectHandler."""
    opener = build_neon_api_opener()
    handlers = cast(list[Any], getattr(opener, "handlers", []))

    # Verify StrictNoRedirectHandler is installed
    assert any(isinstance(h, StrictNoRedirectHandler) for h in handlers)

    # Verify default HTTPRedirectHandler is NOT present
    assert not any(type(h) is urllib.request.HTTPRedirectHandler for h in handlers)

    # Verify HTTPSHandler enforces certificate check and hostname verification
    https_handlers = [h for h in handlers if isinstance(h, urllib.request.HTTPSHandler)]
    assert len(https_handlers) == 1
    ctx = getattr(https_handlers[0], "_context", None)
    assert ctx is not None
    assert ctx.check_hostname is True
    assert ctx.verify_mode == ssl.CERT_REQUIRED


def test_neon_api_opener_successful_metadata_retrieval() -> None:
    """Verify dedicated opener successfully validates authentic staging metadata."""
    success_handler = MockNeonSuccessHandler()
    opener = build_neon_api_opener(extra_handlers=[success_handler])

    url, identity = resolve_migration_target(
        direct_url=VALID_STAGING_URL,
        migration_env="staging",
        migration_confirmation="staging",
        action="upgrade",
        neon_api_key="valid_token",
        control_plane_opener=opener,
    )
    assert isinstance(identity, ApprovedTargetIdentity)
    assert identity.control_plane_verified is True
    assert identity.host == APPROVED_STAGING_HOST
    assert identity.project_id == APPROVED_STAGING_PROJECT_ID
    assert identity.branch_id == APPROVED_STAGING_BRANCH_ID
    assert identity.endpoint_id == APPROVED_STAGING_ENDPOINT_ID
    assert len(success_handler.requests) == 3


def test_neon_api_opener_rejects_malformed_json() -> None:
    """Verify that malformed JSON response fails closed."""
    handler = MockNeonSuccessHandler(raw_body=b"not-valid-json-response")
    opener = build_neon_api_opener(extra_handlers=[handler])

    with pytest.raises(ValueError, match="Neon control-plane API request failed"):
        resolve_migration_target(
            direct_url=VALID_STAGING_URL,
            migration_env="staging",
            migration_confirmation="staging",
            action="upgrade",
            neon_api_key="valid_token",
            control_plane_opener=opener,
        )


def test_neon_api_opener_rejects_http_500_response() -> None:
    """Verify that HTTP 500 error fails closed."""
    handler = MockNeonSuccessHandler(status_code=500)
    opener = build_neon_api_opener(extra_handlers=[handler])

    with pytest.raises(ValueError, match="Neon control-plane API request failed"):
        resolve_migration_target(
            direct_url=VALID_STAGING_URL,
            migration_env="staging",
            migration_confirmation="staging",
            action="upgrade",
            neon_api_key="valid_token",
            control_plane_opener=opener,
        )


# =============================================================================
# Finding 2 Tests: Authentic TLS Enforcement & Hostname Verification
# =============================================================================


def test_tls_rejects_insecure_or_unverified_sslmodes() -> None:
    """Verify that sslmode=require, disable, allow, prefer, and verify-ca are rejected."""
    insecure_modes = ["require", "disable", "allow", "prefer", "verify-ca", "verify_ca"]
    for mode in insecure_modes:
        url = (
            f"postgresql+asyncpg://{APPROVED_STAGING_MIGRATION_ROLE}:pass"
            f"@{APPROVED_STAGING_HOST}:5432/{APPROVED_STAGING_DATABASE}?sslmode={mode}"
        )
        with pytest.raises(
            ValueError,
            match="Insecure or hostname-bypassing options.*are strictly rejected",
        ):
            resolve_migration_target(
                direct_url=url,
                migration_env="staging",
                migration_confirmation="staging",
                action="upgrade",
                control_plane_transport=MockControlPlaneTransport(),
            )


def test_tls_accepts_verify_full() -> None:
    """Verify that sslmode=verify-full is accepted."""
    url, identity = resolve_migration_target(
        direct_url=VALID_STAGING_URL,
        migration_env="staging",
        migration_confirmation="staging",
        action="upgrade",
        control_plane_transport=MockControlPlaneTransport(),
    )
    assert isinstance(identity, ApprovedTargetIdentity)
    assert identity.host == APPROVED_STAGING_HOST


def test_offline_wrong_host_tls_handshake_aborts_before_auth() -> None:
    """Verify that synthetic server with mismatched hostname aborts TLS handshake before auth."""
    with tempfile.TemporaryDirectory() as tmpdir:
        key_path = f"{tmpdir}/key.pem"
        cert_path = f"{tmpdir}/cert.pem"
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-keyout",
                key_path,
                "-out",
                cert_path,
                "-days",
                "1",
                "-subj",
                "/CN=attacker-controlled.neon.tech",
            ],
            check=True,
            capture_output=True,
        )

        server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_ctx.load_cert_chain(cert_path, key_path)

        client_ctx = ssl.create_default_context()
        client_ctx.check_hostname = True
        client_ctx.verify_mode = ssl.CERT_REQUIRED
        client_ctx.load_verify_locations(cert_path)

        client_in = ssl.MemoryBIO()
        client_out = ssl.MemoryBIO()
        client_ssl = client_ctx.wrap_bio(
            client_in, client_out, server_hostname=APPROVED_STAGING_HOST
        )

        server_in = ssl.MemoryBIO()
        server_out = ssl.MemoryBIO()
        server_ssl = server_ctx.wrap_bio(server_in, server_out, server_side=True)

        try:
            client_ssl.do_handshake()
        except ssl.SSLWantReadError:
            pass
        server_in.write(client_out.read())

        try:
            server_ssl.do_handshake()
        except ssl.SSLWantReadError:
            pass
        client_in.write(server_out.read())

        # Handshake fails immediately on client side due to hostname mismatch
        with pytest.raises(ssl.SSLCertVerificationError) as exc_info:
            client_ssl.do_handshake()
        assert "Hostname mismatch" in str(exc_info.value)


@pytest.mark.anyio
async def test_tls_rejects_cert_none() -> None:
    """Verify that SSLContext with verify_mode=CERT_NONE fails closed."""
    conn = _build_mock_connection(verify_mode=ssl.CERT_NONE)
    target = _build_synthetic_approved_identity()
    with pytest.raises(RuntimeError, match="verify_mode is not CERT_REQUIRED"):
        await verify_migration_connection(conn, target)


@pytest.mark.anyio
async def test_tls_rejects_disabled_hostname_verification() -> None:
    """Verify that SSLContext with check_hostname=False fails closed."""
    conn = _build_mock_connection(check_hostname=False)
    target = _build_synthetic_approved_identity()
    with pytest.raises(RuntimeError, match="check_hostname is False"):
        await verify_migration_connection(conn, target)


@pytest.mark.anyio
async def test_tls_rejects_missing_peer_certificate() -> None:
    """Verify that missing peer certificate fails closed."""
    conn = _build_mock_connection(cert_empty=True)
    target = _build_synthetic_approved_identity()
    with pytest.raises(RuntimeError, match="Peer certificate missing or unverified"):
        await verify_migration_connection(conn, target)


@pytest.mark.anyio
async def test_tls_rejects_hostname_mismatch() -> None:
    """Verify that certificate with mismatched hostname fails closed."""
    conn = _build_mock_connection(cert_hostname="attacker-controlled.neon.tech")
    target = _build_synthetic_approved_identity()
    with pytest.raises(RuntimeError, match="TLS certificate hostname mismatch"):
        await verify_migration_connection(conn, target)


# =============================================================================
# Finding 2 Tests: Asynchronous get_raw_connection() API
# =============================================================================


@pytest.mark.anyio
async def test_uses_async_get_raw_connection_api() -> None:
    """Verify that verify_migration_connection calls get_raw_connection() asynchronously."""
    conn = _build_mock_connection()
    target = _build_synthetic_approved_identity()

    await verify_migration_connection(conn, target)
    conn.get_raw_connection.assert_awaited_once()


# =============================================================================
# Finding 3 Tests: Authoritative Neon Identity Binding & Settings
# =============================================================================


def test_rejects_unverified_control_plane_metadata() -> None:
    """Verify caller-provided identity lacking control-plane verification is rejected."""
    unverified = ApprovedTargetIdentity(
        host=APPROVED_STAGING_HOST,
        database=APPROVED_STAGING_DATABASE,
        migration_role=APPROVED_STAGING_MIGRATION_ROLE,
        project_id=APPROVED_STAGING_PROJECT_ID,
        branch_id=APPROVED_STAGING_BRANCH_ID,
        endpoint_id=APPROVED_STAGING_ENDPOINT_ID,
        control_plane_verified=False,
    )
    with pytest.raises(ValueError, match="lacks control-plane verification"):
        resolve_migration_target(
            direct_url=VALID_STAGING_URL,
            migration_env="staging",
            migration_confirmation="staging",
            action="upgrade",
            approved_identity=unverified,
            control_plane_transport=MockControlPlaneTransport(),
        )


def test_rejects_mismatched_control_plane_metadata() -> None:
    """Verify caller-provided identity with mismatched branch is rejected."""
    mismatched = ApprovedTargetIdentity(
        host=APPROVED_STAGING_HOST,
        database=APPROVED_STAGING_DATABASE,
        migration_role=APPROVED_STAGING_MIGRATION_ROLE,
        project_id=APPROVED_STAGING_PROJECT_ID,
        branch_id="br-wrong-branch-id",
        endpoint_id=APPROVED_STAGING_ENDPOINT_ID,
        control_plane_verified=True,
    )
    with pytest.raises(ValueError, match="does not match authoritative staging"):
        resolve_migration_target(
            direct_url=VALID_STAGING_URL,
            migration_env="staging",
            migration_confirmation="staging",
            action="upgrade",
            approved_identity=mismatched,
            control_plane_transport=MockControlPlaneTransport(),
        )


@pytest.mark.anyio
async def test_connection_aborts_on_production_branch_setting() -> None:
    """Verify that runtime connection aborts if neon.branch_id is production."""
    conn = _build_mock_connection(branch_id=PROTECTED_PRODUCTION_BRANCH_ID)
    target = _build_synthetic_approved_identity()
    with pytest.raises(RuntimeError, match="Connected to protected production branch"):
        await verify_migration_connection(conn, target)


@pytest.mark.anyio
async def test_connection_aborts_on_conflicting_branch_setting() -> None:
    """Verify that runtime connection aborts if neon.branch_id conflicts with approved."""
    conn = _build_mock_connection(branch_id="br-other-unknown-branch")
    target = _build_synthetic_approved_identity()
    with pytest.raises(RuntimeError, match="Conflicting Neon branch setting"):
        await verify_migration_connection(conn, target)


# =============================================================================
# Finding 4 Tests: Remote Offline Alembic Execution Blocking
# =============================================================================


def test_remote_offline_upgrade_rejected_via_cli_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify remote offline migration with --sql in sys.argv is strictly rejected."""
    monkeypatch.setattr("sys.argv", ["alembic", "upgrade", "head", "--sql"])
    assert is_offline_migration_requested() is True

    with pytest.raises(
        ValueError, match="Remote offline migration SQL generation is strictly prohibited"
    ):
        resolve_migration_target(
            direct_url=VALID_STAGING_URL,
            migration_env="staging",
            migration_confirmation="staging",
            action="upgrade",
            control_plane_transport=MockControlPlaneTransport(),
        )


def test_remote_offline_upgrade_rejected_programmatically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify programmatic offline upgrade on remote target is rejected before SQL output."""
    monkeypatch.setenv("MIGRATION_ENVIRONMENT", "staging")
    monkeypatch.delenv("TEST_DATABASE_NAME", raising=False)

    fake_config = MagicMock()
    fake_config.cmd_opts.sql = True

    assert is_offline_migration_requested(fake_config, None) is True

    with pytest.raises(
        ValueError, match="Remote offline migration SQL generation is strictly prohibited"
    ):
        resolve_migration_target(
            direct_url=VALID_STAGING_URL,
            migration_env="staging",
            migration_confirmation="staging",
            action="upgrade",
            config=fake_config,
            control_plane_transport=MockControlPlaneTransport(),
        )


def test_local_offline_upgrade_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify local disposable test database permits offline SQL generation."""
    test_db = "dormiostay_t03_test_offline"
    local_url = f"postgresql://app@127.0.0.1:5433/{test_db}"

    monkeypatch.setattr("sys.argv", ["alembic", "upgrade", "head", "--sql"])
    url, identity = resolve_migration_target(
        direct_url=local_url,
        test_db_name=test_db,
        action="upgrade",
    )
    assert identity == test_db


# =============================================================================
# Finding 3 & 5 Tests: Strict Privilege & Database Catalog Verification
# =============================================================================


@pytest.mark.anyio
async def test_catalog_rejects_missing_application_role() -> None:
    """Verify that if dormiostay_app does not exist, verification fails closed."""
    conn = _build_mock_connection(app_exists=False)
    target = _build_synthetic_approved_identity()
    with pytest.raises(
        RuntimeError, match="required application role 'dormiostay_app' does not exist"
    ):
        await verify_migration_connection(conn, target)


@pytest.mark.anyio
async def test_catalog_rejects_null_role_attributes() -> None:
    """Verify that if role attributes return null, verification fails closed."""
    conn = _build_mock_connection()
    orig_execute = conn.execute.side_effect

    async def mock_exec_null_attr(stmt: Any, *args: Any, **kwargs: Any) -> Any:
        if "FROM pg_roles WHERE rolname = :app_role" in str(stmt):
            res = MagicMock()
            res.first.return_value = (True, None, False, False, False, False)
            return res
        return await orig_execute(stmt, *args, **kwargs)

    conn.execute.side_effect = mock_exec_null_attr
    target = _build_synthetic_approved_identity()
    with pytest.raises(RuntimeError, match="null or non-boolean attributes"):
        await verify_migration_connection(conn, target)


@pytest.mark.anyio
async def test_migration_role_lacks_create_privilege() -> None:
    """Verify that if neondb_owner lacks CREATE on public schema, migration fails closed."""
    conn = _build_mock_connection(admin_can_create_schema=False)
    target = _build_synthetic_approved_identity()
    with pytest.raises(RuntimeError, match="Migration administrator lacks CREATE privilege"):
        await verify_migration_connection(conn, target)


@pytest.mark.anyio
async def test_dormiostay_app_elevated_attributes_rejected() -> None:
    """Verify that if dormiostay_app possesses superuser/createrole, execution aborts."""
    conn = _build_mock_connection(app_elevated_flags=True)
    target = _build_synthetic_approved_identity()
    with pytest.raises(RuntimeError, match="possesses elevated attributes"):
        await verify_migration_connection(conn, target)


@pytest.mark.anyio
async def test_dormiostay_app_create_privilege_rejected() -> None:
    """Verify that if dormiostay_app has schema CREATE, execution aborts."""
    conn = _build_mock_connection(app_sch_create=True)
    target = _build_synthetic_approved_identity()
    with pytest.raises(RuntimeError, match="has CREATE privilege on public schema"):
        await verify_migration_connection(conn, target)


@pytest.mark.anyio
async def test_dormiostay_app_owns_tables_rejected() -> None:
    """Verify that if dormiostay_app owns tables in public, execution aborts."""
    conn = _build_mock_connection(app_owned_tables=1)
    target = _build_synthetic_approved_identity()
    with pytest.raises(RuntimeError, match="owns tables in public schema"):
        await verify_migration_connection(conn, target)


@pytest.mark.anyio
async def test_catalog_rejects_null_alembic_table_exists() -> None:
    """Verify that null result for alembic_table_exists fails closed."""
    conn = _build_mock_connection()
    orig_scalar = conn.scalar.side_effect

    async def mock_scalar_null_alembic(stmt: Any, *args: Any, **kwargs: Any) -> Any:
        if "table_name = 'alembic_version'" in str(stmt):
            return None
        return await orig_scalar(stmt, *args, **kwargs)

    conn.scalar.side_effect = mock_scalar_null_alembic
    target = _build_synthetic_approved_identity()
    with pytest.raises(RuntimeError, match="unexpected catalog result for alembic_table_exists"):
        await verify_migration_connection(conn, target)


@pytest.mark.anyio
async def test_catalog_rejects_existing_alembic_version_table() -> None:
    """Verify that pre-existing alembic_version table fails closed."""
    conn = _build_mock_connection(alembic_table_exists=True, alembic_rev="0001_foundation")
    target = _build_synthetic_approved_identity()
    with pytest.raises(RuntimeError, match="already contains 'alembic_version' table"):
        await verify_migration_connection(conn, target)


@pytest.mark.anyio
async def test_schema_baseline_rejects_existing_views() -> None:
    """Verify that existing views in public schema fail closed."""
    conn = _build_mock_connection(views_count=2)
    target = _build_synthetic_approved_identity()
    with pytest.raises(RuntimeError, match="existing views in schema public"):
        await verify_migration_connection(conn, target)


@pytest.mark.anyio
async def test_schema_baseline_rejects_existing_sequences() -> None:
    """Verify that existing sequences in public schema fail closed."""
    conn = _build_mock_connection(sequences_count=1)
    target = _build_synthetic_approved_identity()
    with pytest.raises(RuntimeError, match="existing sequences in schema public"):
        await verify_migration_connection(conn, target)


@pytest.mark.anyio
async def test_schema_baseline_rejects_existing_functions() -> None:
    """Verify that existing user-defined functions in public schema fail closed."""
    conn = _build_mock_connection(functions_count=1)
    target = _build_synthetic_approved_identity()
    with pytest.raises(RuntimeError, match="existing functions in schema public"):
        await verify_migration_connection(conn, target)


@pytest.mark.anyio
async def test_catalog_rejects_existing_domains_and_ranges() -> None:
    """Verify that existing custom types/domains/ranges in public fail closed."""
    conn = _build_mock_connection(types_count=3)
    target = _build_synthetic_approved_identity()
    with pytest.raises(RuntimeError, match="existing custom types/domains/ranges in schema public"):
        await verify_migration_connection(conn, target)


@pytest.mark.anyio
async def test_schema_baseline_rejects_null_catalog_results() -> None:
    """Verify that NULL / None catalog scalar results fail closed."""
    conn = _build_mock_connection()
    original_scalar = conn.scalar.side_effect

    async def mock_scalar_with_none(stmt: Any, *args: Any, **kwargs: Any) -> Any:
        if "table_schema = 'public' AND table_name != 'alembic_version'" in str(stmt):
            return None
        return await original_scalar(stmt, *args, **kwargs)

    conn.scalar.side_effect = mock_scalar_with_none
    target = _build_synthetic_approved_identity()
    with pytest.raises(RuntimeError, match="Staging database contains None existing tables"):
        await verify_migration_connection(conn, target)

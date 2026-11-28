"""Guards for Alembic database migrations.

Enforces fail-closed validation:
1. Disposable test databases:
   Must match `dormiostay_t03_test_[a-z0-9_]+` on localhost / 127.0.0.1 / ::1.
   Permits both upgrade and downgrade operations for automated testing.
   Permits offline SQL generation for local testing.
2. Verified Staging migrations (Gate 3):
   Permits strictly confirmed remote staging UPGRADES against the authoritative
   Neon staging branch (small-dew-81510029 / br-weathered-dust-bim5fibs / neondb)
   executed exclusively by `neondb_owner`.
   Enforces authentic TLS with trusted CA certificate chain validation and
   strict hostname matching.
   Binds control-plane metadata to the database connection via TLS and catalog verification.
   Enforces empty schema baseline (tables, views, matviews, sequences, functions, types).
   Asserts application role `dormiostay_app` has no CREATE or DDL privileges or object ownership.
3. Production and Remote Offline / Downgrade Protections:
   ALL production migrations (upgrades, downgrades, offline SQL generation) and
   ALL remote downgrades remain strictly DISABLED and fail closed.
   Remote offline migration SQL generation (--sql) is strictly prohibited.
   Application role `dormiostay_app` is strictly forbidden from executing migrations.
"""

from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import parse_qs, urlparse

from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.session import async_url
from app.db.test_target import validate_test_url, verify_test_connection

# Authoritative Staging Invariants (Verified in T-05 Gates 1 & 2)
APPROVED_STAGING_PROJECT_ID: str = "small-dew-81510029"
APPROVED_STAGING_BRANCH_ID: str = "br-weathered-dust-bim5fibs"
APPROVED_STAGING_HOST: str = "ep-cold-smoke-bir1s74t.c-5.ap-southeast-1.aws.neon.tech"
APPROVED_STAGING_ENDPOINT_ID: str = "ep-cold-smoke-bir1s74t"
APPROVED_STAGING_DATABASE: str = "neondb"
APPROVED_STAGING_MIGRATION_ROLE: str = "neondb_owner"

# Strictly Protected Production Invariants
PROTECTED_PRODUCTION_PROJECT_ID: str = "small-dew-81510029"
PROTECTED_PRODUCTION_BRANCH_ID: str = "br-holy-lake-bi0hc1lu"

# Forbidden Application Role (must NEVER run migrations or own schema objects)
FORBIDDEN_APP_ROLE: str = "dormiostay_app"

REMOTE_DISABLED_MSG: str = (
    "Remote database migrations are strictly disabled until independently "
    "verified staging infrastructure is available"
)
LOCAL_DISPOSABLE_REQUIRED_MSG: str = (
    "An explicitly approved disposable database name is required "
    "(TEST_DATABASE_NAME); remote migrations are disabled"
)
REMOTE_OFFLINE_PROHIBITED_MSG: str = (
    "Remote offline migration SQL generation is strictly prohibited"
)


@dataclass(frozen=True)
class ApprovedTargetIdentity:
    """Authoritative target identity established from authenticated control-plane metadata."""

    host: str
    database: str
    migration_role: str
    project_id: str | None = None
    branch_id: str | None = None
    endpoint_id: str | None = None
    control_plane_verified: bool = False


class StrictNoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """HTTP redirect handler that strictly rejects all 3xx redirects by security policy.

    Overrides redirect_request and status-specific handlers (301, 302, 303, 307, 308)
    to prevent any follow-up request or bearer token leakage to redirected origins.
    Rejects redirects with fixed, sanitized messages containing only the status code,
    strictly omitting Location headers, destination URLs, tokens, and query parameters.
    """

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        raise ValueError(f"Neon API HTTP redirect ({code}) rejected by security policy")

    def http_error_301(
        self, req: urllib.request.Request, fp: Any, code: int, msg: str, headers: Any
    ) -> Any:
        raise ValueError("Neon API HTTP redirect (301) rejected by security policy")

    def http_error_302(
        self, req: urllib.request.Request, fp: Any, code: int, msg: str, headers: Any
    ) -> Any:
        raise ValueError("Neon API HTTP redirect (302) rejected by security policy")

    def http_error_303(
        self, req: urllib.request.Request, fp: Any, code: int, msg: str, headers: Any
    ) -> Any:
        raise ValueError("Neon API HTTP redirect (303) rejected by security policy")

    def http_error_307(
        self, req: urllib.request.Request, fp: Any, code: int, msg: str, headers: Any
    ) -> Any:
        raise ValueError("Neon API HTTP redirect (307) rejected by security policy")

    def http_error_308(
        self, req: urllib.request.Request, fp: Any, code: int, msg: str, headers: Any
    ) -> Any:
        raise ValueError("Neon API HTTP redirect (308) rejected by security policy")


def build_neon_api_opener(
    ssl_context: ssl.SSLContext | None = None,
    extra_handlers: list[urllib.request.BaseHandler] | None = None,
) -> urllib.request.OpenerDirector:
    """Build dedicated HTTPS opener configured to reject redirects and enforce TLS verification.

    Never uses default urllib.request.urlopen().
    Replaces default HTTPRedirectHandler with StrictNoRedirectHandler.
    Configures HTTPSHandler with authentic TLS verification (CERT_REQUIRED, check_hostname).
    """
    ctx = ssl_context
    if ctx is None:
        ctx = ssl.create_default_context()
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED

    handlers: list[urllib.request.BaseHandler] = [
        StrictNoRedirectHandler(),
        urllib.request.HTTPSHandler(context=ctx),
    ]
    if extra_handlers:
        handlers.extend(extra_handlers)

    return urllib.request.build_opener(*handlers)


def verify_neon_control_plane(
    api_key: str | None = None,
    base_url: str = "https://console.neon.tech/api/v2",
    timeout: float = 10.0,
    transport: Any = None,
    opener: urllib.request.OpenerDirector | None = None,
    require_read_write: bool = False,
) -> ApprovedTargetIdentity:
    """Fetch and verify fresh staging metadata from authenticated Neon API.

    Verifies project ID, branch ID, endpoint ID, hostname, and ensures the endpoint
    belongs to the approved staging branch.
    Enforces authentic TLS, hostname verification, and strictly rejects HTTP redirects.
    Fails closed when Neon API is unavailable, unauthenticated, or metadata conflicts.
    """
    key = api_key or os.environ.get("NEON_API_KEY")
    if not key and transport is None:
        raise ValueError(
            "NEON_API_KEY is required for authoritative control-plane metadata verification"
        )

    # 1. Fetch metadata via HTTP transport or dedicated redirect-rejecting opener
    if transport is not None:
        try:
            proj_data = transport.get(f"/projects/{APPROVED_STAGING_PROJECT_ID}")
            branches_data = transport.get(f"/projects/{APPROVED_STAGING_PROJECT_ID}/branches")
            endpoints_data = transport.get(f"/projects/{APPROVED_STAGING_PROJECT_ID}/endpoints")
        except Exception as exc:
            raise ValueError(f"Neon control-plane API request failed: {exc}") from None
    else:
        api_opener = opener or build_neon_api_opener()
        headers = {
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "User-Agent": "DormioStay-MigrationGuard/1.0",
        }

        def _do_get(path: str) -> dict[str, Any]:
            url = f"{base_url.rstrip('/')}{path}"
            req = urllib.request.Request(url, headers=headers, method="GET")
            try:
                with api_opener.open(req, timeout=timeout) as resp:
                    if resp.status != 200:
                        raise ValueError(f"Neon API returned HTTP {resp.status}")
                    body = resp.read()
                    data = json.loads(body.decode("utf-8"))
                    if not isinstance(data, dict):
                        raise ValueError(f"Expected JSON object from {path}")
                    return cast(dict[str, Any], data)
            except Exception as exc:
                raise ValueError(f"Neon control-plane API request failed: {exc}") from None

        proj_data = _do_get(f"/projects/{APPROVED_STAGING_PROJECT_ID}")
        branches_data = _do_get(f"/projects/{APPROVED_STAGING_PROJECT_ID}/branches")
        endpoints_data = _do_get(f"/projects/{APPROVED_STAGING_PROJECT_ID}/endpoints")

    # 2. Validate project
    project = proj_data.get("project", proj_data)
    if not isinstance(project, dict) or project.get("id") != APPROVED_STAGING_PROJECT_ID:
        actual_id = project.get("id") if isinstance(project, dict) else "unknown"
        raise ValueError(
            f"Control-plane project ID mismatch: expected '{APPROVED_STAGING_PROJECT_ID}', "
            f"got '{actual_id}'"
        )

    # 3. Validate branches
    branches = branches_data.get("branches", [])
    if not isinstance(branches, list):
        raise ValueError("Invalid control-plane branches response: expected list")

    staging_branch = next(
        (b for b in branches if isinstance(b, dict) and b.get("id") == APPROVED_STAGING_BRANCH_ID),
        None,
    )
    if not staging_branch:
        raise ValueError(
            f"Staging branch '{APPROVED_STAGING_BRANCH_ID}' not found in Neon control plane"
        )

    if staging_branch.get("name") != "staging":
        raise ValueError(
            f"Branch name mismatch for '{APPROVED_STAGING_BRANCH_ID}': "
            f"expected 'staging', got '{staging_branch.get('name')}'"
        )

    # 4. Validate endpoints
    endpoints = endpoints_data.get("endpoints", [])
    if not isinstance(endpoints, list):
        raise ValueError("Invalid control-plane endpoints response: expected list")

    staging_endpoints = [
        ep
        for ep in endpoints
        if isinstance(ep, dict) and ep.get("branch_id") == APPROVED_STAGING_BRANCH_ID
    ]
    if not staging_endpoints:
        raise ValueError(
            f"No compute endpoints found for staging branch '{APPROVED_STAGING_BRANCH_ID}'"
        )

    matching_ep = next(
        (ep for ep in staging_endpoints if ep.get("host") == APPROVED_STAGING_HOST),
        None,
    )
    if not matching_ep:
        found_hosts = [ep.get("host") for ep in staging_endpoints]
        raise ValueError(
            f"Control-plane endpoint mismatch. Expected host '{APPROVED_STAGING_HOST}', "
            f"found: {found_hosts}"
        )

    if require_read_write and matching_ep.get("type") != "read_write":
        raise ValueError("Approved staging endpoint must be read-write")

    ep_id = matching_ep.get("id")
    if ep_id != APPROVED_STAGING_ENDPOINT_ID:
        raise ValueError(
            f"Control-plane endpoint ID mismatch: expected '{APPROVED_STAGING_ENDPOINT_ID}', "
            f"got '{ep_id}'"
        )

    if matching_ep.get("branch_id") != APPROVED_STAGING_BRANCH_ID:
        raise ValueError(
            f"Security violation: endpoint '{ep_id}' does not belong to staging branch "
            f"'{APPROVED_STAGING_BRANCH_ID}'"
        )

    return ApprovedTargetIdentity(
        host=APPROVED_STAGING_HOST,
        database=APPROVED_STAGING_DATABASE,
        migration_role=APPROVED_STAGING_MIGRATION_ROLE,
        project_id=APPROVED_STAGING_PROJECT_ID,
        branch_id=APPROVED_STAGING_BRANCH_ID,
        endpoint_id=APPROVED_STAGING_ENDPOINT_ID,
        control_plane_verified=True,
    )


def get_approved_staging_identity(
    api_key: str | None = None,
    base_url: str = "https://console.neon.tech/api/v2",
    timeout: float = 10.0,
    transport: Any = None,
    opener: urllib.request.OpenerDirector | None = None,
) -> ApprovedTargetIdentity:
    """Return authoritative staging target identity established by authenticated control plane."""
    return verify_neon_control_plane(
        api_key=api_key,
        base_url=base_url,
        timeout=timeout,
        transport=transport,
        opener=opener,
    )


def _match_cert_hostname(peer_cert: dict[str, Any], expected_host: str) -> None:
    """Verify that peer certificate matches expected hostname.

    Supports Python <3.12 ssl.match_hostname and Python 3.12+ manual RFC 6125 matching.
    """
    if hasattr(ssl, "match_hostname"):
        try:
            ssl.match_hostname(peer_cert, expected_host)
            return
        except Exception as exc:
            raise RuntimeError(
                f"TLS certificate hostname mismatch for '{expected_host}': {exc}"
            ) from None

    # Python 3.12+ fallback: extract DNS Subject Alternative Names or Common Name
    san_entries = peer_cert.get("subjectAltName", ())
    dns_names: list[str] = [
        val for typ, val in san_entries if typ == "DNS" and isinstance(val, str)
    ]
    if not dns_names:
        for rdn in peer_cert.get("subject", ()):
            for key, val in rdn:
                if key == "commonName" and isinstance(val, str):
                    dns_names.append(val)

    expected_lower = expected_host.lower()
    for name in dns_names:
        name_lower = name.lower()
        if name_lower == expected_lower:
            return
        if name_lower.startswith("*."):
            suffix = name_lower[1:]
            if expected_lower.endswith(suffix):
                prefix = expected_lower[: -len(suffix)]
                if prefix and "." not in prefix:
                    return

    raise RuntimeError(
        f"TLS certificate hostname mismatch for '{expected_host}': "
        f"certificate names {dns_names} do not match expected host."
    )


async def _verify_client_tls(connection: Any, expected_host: str) -> None:
    """Verify authentic TLS using certificate chain and hostname verification.

    Uses the asynchronous AsyncConnection.get_raw_connection() API to inspect the
    underlying driver connection and transport SSL session.
    Fails closed on missing TLS, CERT_NONE, disabled hostname verification,
    or certificate hostname mismatch.
    """
    # Finding 2: Correctly use get_raw_connection() asynchronously if available
    driver_conn: Any = None
    if hasattr(connection, "get_raw_connection") and callable(connection.get_raw_connection):
        try:
            raw = await connection.get_raw_connection()
            driver_conn = getattr(raw, "driver_connection", raw)
        except Exception as exc:
            raise RuntimeError(f"Failed to acquire raw driver connection: {exc}") from None
    else:
        driver_conn = connection

    # Extract transport from driver connection
    transport = getattr(driver_conn, "_transport", None)
    if transport is None:
        transport = getattr(connection, "_transport", None)

    if transport is None:
        raise RuntimeError(
            "Database connection lacks active network transport. Insecure connection."
        )

    ssl_obj = None
    if hasattr(transport, "get_extra_info"):
        ssl_obj = transport.get_extra_info("ssl_object")

    if ssl_obj is None:
        raise RuntimeError(
            "Client connection reports TLS is not active. Insecure migration terminated."
        )

    # Finding 1: Verify authentic SSLContext configuration
    ssl_ctx = getattr(ssl_obj, "context", None)
    if ssl_ctx is None and hasattr(transport, "get_extra_info"):
        ssl_ctx = transport.get_extra_info("sslcontext")

    if ssl_ctx is not None:
        verify_mode = getattr(ssl_ctx, "verify_mode", None)
        if verify_mode != ssl.CERT_REQUIRED:
            raise RuntimeError(
                "Insecure TLS configuration: verify_mode is not CERT_REQUIRED "
                "(untrusted certificates permitted)."
            )
        check_hostname = getattr(ssl_ctx, "check_hostname", False)
        if not check_hostname:
            raise RuntimeError(
                "Insecure TLS configuration: check_hostname is False "
                "(hostname verification disabled)."
            )

    # Finding 1: Verify peer certificate and hostname matching
    if hasattr(ssl_obj, "getpeercert"):
        peer_cert = ssl_obj.getpeercert()
        if not peer_cert:
            raise RuntimeError("Peer certificate missing or unverified by TLS handshake.")
        if isinstance(peer_cert, dict):
            _match_cert_hostname(peer_cert, expected_host)


def is_offline_migration_requested(
    config: object | None = None,
    context_obj: object | None = None,
) -> bool:
    """Detect whether Alembic offline SQL generation (--sql) was requested."""
    if config is None and context_obj is None:
        try:
            from alembic import context

            config = getattr(context, "config", None)
            context_obj = getattr(context, "_proxy", None)
        except Exception:
            pass

    # 1. EnvironmentContext programmatic inspection
    if context_obj is not None:
        if hasattr(context_obj, "is_offline_mode") and callable(context_obj.is_offline_mode):
            try:
                if context_obj.is_offline_mode():
                    return True
            except Exception:
                pass
        opts = getattr(context_obj, "context_opts", None)
        if isinstance(opts, dict) and opts.get("as_sql", False) is True:
            return True

    # 2. Config command options (CLI or programmatic with cmd_opts)
    if config is not None and hasattr(config, "cmd_opts") and config.cmd_opts is not None:
        if getattr(config.cmd_opts, "sql", False) is True:
            return True

    # 3. CLI arguments inspection
    for arg in sys.argv[1:]:
        arg_lower = arg.lower()
        if arg_lower in {"--sql", "-s"} or arg_lower.startswith("--sql="):
            return True

    # 4. Environment flag
    if os.environ.get("MIGRATION_OFFLINE") in {"1", "true", "yes"} or os.environ.get(
        "ALEMBIC_OFFLINE"
    ) in {"1", "true", "yes"}:
        return True

    return False


def detect_migration_action(
    config: object | None = None,
    context_obj: object | None = None,
) -> str:
    """Detect whether the current migration command is an upgrade or downgrade.

    Fail-safe: any signal indicating downgrade (CLI args, programmatic context_opts,
    config command) classifies the action as 'downgrade'. MIGRATION_ACTION=upgrade
    CANNOT override an actual downgrade.
    """
    # 1. Programmatic Alembic execution via EnvironmentContext (e.g. command.downgrade)
    if context_obj is not None and hasattr(context_obj, "context_opts"):
        opts = getattr(context_obj, "context_opts", None)
        if isinstance(opts, dict):
            fn = opts.get("fn")
            if fn is not None:
                fn_name = str(getattr(fn, "__name__", fn)).lower()
                if "downgrade" in fn_name:
                    return "downgrade"
            dest = str(opts.get("destination_rev", "") or "").lower()
            if (
                dest in {"base", "-1"}
                or (dest.startswith("-") and dest[1:].isdigit())
                or "head:base" in dest
                or ":base" in dest
            ):
                return "downgrade"

    # 2. Config command options (CLI or programmatic with cmd_opts set)
    if config is not None and hasattr(config, "cmd_opts") and config.cmd_opts is not None:
        cmd = getattr(config.cmd_opts, "cmd", None)
        if cmd and isinstance(cmd, tuple) and len(cmd) > 0:
            cmd_name = str(getattr(cmd[0], "__name__", cmd[0])).lower()
            if "downgrade" in cmd_name:
                return "downgrade"
        rev = getattr(config.cmd_opts, "revision", None)
        if rev is not None:
            rev_str = str(rev).lower()
            if (
                rev_str in {"base", "-1"}
                or (rev_str.startswith("-") and rev_str[1:].isdigit())
                or "head:base" in rev_str
                or ":base" in rev_str
            ):
                return "downgrade"

    # 3. CLI arguments inspection
    for arg in sys.argv[1:]:
        arg_lower = arg.lower()
        if (
            arg_lower in {"downgrade", "downgrading", "base"}
            or arg_lower == "-1"
            or (arg_lower.startswith("-") and arg_lower[1:].isdigit())
            or "head:base" in arg_lower
            or ":base" in arg_lower
        ):
            return "downgrade"

    # 4. Caller-declared action: only accepted if NO downgrade signal was detected
    env_action = os.environ.get("MIGRATION_ACTION")
    if env_action:
        env_action_lower = env_action.lower()
        if env_action_lower in {"downgrade", "downgrading"}:
            return "downgrade"
        if env_action_lower in {"upgrade", "upgrading"}:
            return "upgrade"

    return "upgrade"


def resolve_migration_target(
    direct_url: str | None,
    test_db_name: str | None = None,
    migration_env: str | None = None,
    migration_confirmation: str | None = None,
    action: str = "upgrade",
    approved_identity: ApprovedTargetIdentity | None = None,
    config: object | None = None,
    context_obj: object | None = None,
    control_plane_transport: Any = None,
    neon_api_key: str | None = None,
    control_plane_opener: urllib.request.OpenerDirector | None = None,
) -> tuple[URL, ApprovedTargetIdentity | str]:
    """Validate and resolve migration target URL.

    Security Policy:
    - Production remote migrations are strictly disabled in T-05.
    - Remote database downgrade operations are strictly rejected by policy.
    - Remote offline migration SQL generation is strictly prohibited.
    - Remote staging upgrade is authorized only when targeting verified staging infrastructure
      with explicit confirmation and exact identity match.
    - Application role 'dormiostay_app' is forbidden from running migrations.
    - Local disposable test databases (dormiostay_t03_test_*) on loopback are permitted
      for testing.
    - All other targets fail closed with ValueError.
    """
    if not direct_url:
        raise ValueError("DATABASE_URL_DIRECT is required for migrations")

    try:
        parsed = urlparse(direct_url)
    except Exception as exc:
        raise ValueError(f"Invalid DATABASE_URL_DIRECT: {exc}") from None

    if parsed.scheme not in {"postgresql", "postgresql+asyncpg"}:
        raise ValueError("DATABASE_URL_DIRECT must be a PostgreSQL connection URL")

    # Reject any reference to protected production branch across all modes
    if PROTECTED_PRODUCTION_BRANCH_ID in direct_url:
        raise ValueError("Targeting protected production branch is strictly forbidden")

    host = (parsed.hostname or "").strip("[]").lower()

    # Reject production environment upfront
    if migration_env == "production":
        raise ValueError("Production remote migrations are strictly disabled in T-05")

    # Mode 1: Local loopback targets MUST be approved disposable test databases
    if host in {"127.0.0.1", "localhost", "::1"}:
        if migration_env in {"staging", "production"}:
            raise ValueError("Migration target for staging cannot be a local disposable host")

        if test_db_name:
            url = validate_test_url(direct_url, test_db_name)
            return url, test_db_name

        raise ValueError(LOCAL_DISPOSABLE_REQUIRED_MSG)

    # Finding 4: Reject remote offline migrations upfront
    if is_offline_migration_requested(config, context_obj):
        raise ValueError(REMOTE_OFFLINE_PROHIBITED_MSG)

    # Mode 2: Remote targets — Downgrades strictly rejected
    if action == "downgrade":
        raise ValueError("Remote database downgrade operations are strictly rejected by policy")

    # Mode 3: Remote targets — Must be verified staging environment
    if migration_env != "staging":
        raise ValueError(REMOTE_DISABLED_MSG)

    # Mode 4: Require explicit staging confirmation matching branch or environment label
    valid_confirmations = {"staging", APPROVED_STAGING_BRANCH_ID}
    if not migration_confirmation or migration_confirmation not in valid_confirmations:
        raise ValueError("Explicit migration confirmation matching staging is required")

    # Mode 5: Require non-empty credentials in connection URL
    if not parsed.password:
        raise ValueError("Migration credentials (password) are required in DATABASE_URL_DIRECT")

    # Verify exact staging host before any control-plane calls
    if host != APPROVED_STAGING_HOST.lower():
        raise ValueError(REMOTE_DISABLED_MSG)

    # Finding 1: Fetch and verify fresh metadata from Neon Control Plane
    auth = verify_neon_control_plane(
        api_key=neon_api_key,
        transport=control_plane_transport,
        opener=control_plane_opener,
    )

    # Finding 1: Reject caller-supplied identity if it lacks verification or mismatches
    if approved_identity is not None:
        if not approved_identity.control_plane_verified:
            raise ValueError("Caller-provided approved identity lacks control-plane verification")
        if (
            approved_identity.project_id != auth.project_id
            or approved_identity.branch_id != auth.branch_id
            or approved_identity.endpoint_id != auth.endpoint_id
            or approved_identity.host.lower() != auth.host.lower()
            or approved_identity.database != auth.database
            or approved_identity.migration_role != auth.migration_role
        ):
            raise ValueError(
                "Caller-provided approved identity does not match authoritative staging"
            )

    # Reject application role execution
    username = parsed.username or ""
    if username == FORBIDDEN_APP_ROLE:
        raise ValueError(
            f"Application role '{FORBIDDEN_APP_ROLE}' is strictly forbidden from "
            "executing migrations"
        )
    if username != auth.migration_role:
        raise ValueError(f"Migration user must be '{auth.migration_role}', got '{username}'")

    # Verify exact database name
    dbname = (parsed.path or "").lstrip("/")
    if dbname != auth.database:
        raise ValueError(f"Migration database must be '{auth.database}', got '{dbname}'")

    # Finding 2: Enforce authentic TLS in query parameters
    # (require verify-full, reject verify-ca, require, allow, prefer, disable)
    query_params = parse_qs(parsed.query)
    sslmode_vals = query_params.get("sslmode", [])
    ssl_vals = query_params.get("ssl", [])
    all_ssl = [v.lower() for v in (sslmode_vals + ssl_vals)]

    insecure_ssl_values = {"disable", "allow", "prefer", "require", "verify-ca", "verify_ca"}
    if not all_ssl or any(v in insecure_ssl_values for v in all_ssl):
        raise ValueError(
            "Remote migrations require authentic TLS (sslmode=verify-full). "
            "Insecure or hostname-bypassing options (disable/allow/prefer/require/verify-ca) "
            "are strictly rejected."
        )

    valid_ssl_values = {"verify-full", "verify_full"}
    if not any(v in valid_ssl_values for v in all_ssl):
        raise ValueError("Remote migrations require authentic TLS (sslmode=verify-full)")

    url = async_url(direct_url)
    return url, auth


async def verify_migration_connection(
    connection: AsyncConnection,
    expected_target: ApprovedTargetIdentity | str,
    test_db_name: str | None = None,
) -> None:
    """Verify runtime database connection before executing DDL.

    Enforces:
    1. Loopback disposable test database verification when test_db_name is set.
    2. Authentic TLS verification with certificate chain and hostname matching.
    3. Exact database name match against approved target.
    4. User identity is neondb_owner and NEVER dormiostay_app.
    5. Absence of conflicting Neon engine settings (neon.branch_id != production).
    6. Migration role holds necessary CONNECT, CREATE, USAGE privileges.
    7. Application role dormiostay_app holds no CREATE/DDL privileges or object ownership.
    8. Strict empty schema baseline assertion: zero tables, views, matviews, sequences,
       functions, custom types, and no existing Alembic revision.
    """
    if test_db_name:
        assert isinstance(expected_target, str)
        await verify_test_connection(connection, expected_target)
        return

    if not isinstance(expected_target, ApprovedTargetIdentity):
        raise RuntimeError(REMOTE_DISABLED_MSG)

    if not expected_target.control_plane_verified:
        raise RuntimeError("Approved target identity lacks verified control-plane metadata")

    # Finding 1 & 2: Authentic TLS verification on raw driver connection
    await _verify_client_tls(connection, expected_target.host)

    # 1. Verify exact database target
    actual_db = await connection.scalar(text("SELECT current_database()"))
    if actual_db != expected_target.database:
        raise RuntimeError(
            f"Effective database '{actual_db}' differs from approved '{expected_target.database}'"
        )

    # 2. Verify migration user identity (Assert neondb_owner, strictly forbid dormiostay_app)
    user_res = await connection.execute(text("SELECT current_user, session_user"))
    users = user_res.first()
    if not users:
        raise RuntimeError("Failed to verify database user identity")
    curr_u, sess_u = users[0], users[1]

    if curr_u == FORBIDDEN_APP_ROLE or sess_u == FORBIDDEN_APP_ROLE:
        raise RuntimeError(
            f"Application role '{FORBIDDEN_APP_ROLE}' is strictly forbidden from "
            "executing migrations"
        )
    if curr_u != expected_target.migration_role or sess_u != expected_target.migration_role:
        raise RuntimeError(
            f"Effective user '{curr_u}' differs from approved '{expected_target.migration_role}'"
        )

    # Finding 3: Check optional Neon engine settings (if present, must NOT conflict with approved)
    neon_settings: dict[str, str] = {}
    try:
        settings_rows = await connection.execute(
            text(
                "SELECT name, setting FROM pg_settings WHERE name IN ("
                "'neon.project_id', 'neon.branch_id', 'neon.endpoint_id'"
                ")"
            )
        )
        neon_settings = {str(row[0]): str(row[1]) for row in settings_rows.fetchall()}
    except Exception:
        neon_settings = {}

    actual_branch = neon_settings.get("neon.branch_id")
    if actual_branch == PROTECTED_PRODUCTION_BRANCH_ID:
        raise RuntimeError("CRITICAL: Connected to protected production branch! Aborting.")
    if actual_branch and actual_branch != expected_target.branch_id:
        branch_msg = (
            f"Conflicting Neon branch setting '{actual_branch}' differs from approved "
            f"'{expected_target.branch_id}'"
        )
        raise RuntimeError(branch_msg)

    actual_proj = neon_settings.get("neon.project_id")
    if actual_proj and actual_proj != expected_target.project_id:
        proj_msg = (
            f"Conflicting Neon project setting '{actual_proj}' differs from approved "
            f"'{expected_target.project_id}'"
        )
        raise RuntimeError(proj_msg)

    if expected_target.endpoint_id:
        actual_ep = neon_settings.get("neon.endpoint_id")
        if actual_ep and actual_ep != expected_target.endpoint_id:
            ep_msg = (
                f"Conflicting Neon endpoint setting '{actual_ep}' differs from approved "
                f"'{expected_target.endpoint_id}'"
            )
            raise RuntimeError(ep_msg)

    # Finding 5: Complete strict privilege verification for migration administrator (neondb_owner)
    can_connect = await connection.scalar(
        text("SELECT has_database_privilege(current_user, current_database(), 'CONNECT')")
    )
    if can_connect is not True:
        raise RuntimeError("Migration administrator lacks CONNECT privilege on database")

    can_create_schema = await connection.scalar(
        text("SELECT has_schema_privilege(current_user, 'public', 'CREATE')")
    )
    if can_create_schema is not True:
        raise RuntimeError("Migration administrator lacks CREATE privilege on schema public")

    can_use_schema = await connection.scalar(
        text("SELECT has_schema_privilege(current_user, 'public', 'USAGE')")
    )
    if can_use_schema is not True:
        raise RuntimeError("Migration administrator lacks USAGE privilege on schema public")

    # Finding 3: Complete strict privilege verification for application role (dormiostay_app)
    app_role_res = await connection.execute(
        text(
            "SELECT rolcanlogin, rolsuper, rolcreaterole, rolcreatedb, rolreplication, "
            "rolbypassrls FROM pg_roles WHERE rolname = :app_role"
        ),
        {"app_role": FORBIDDEN_APP_ROLE},
    )
    app_data = app_role_res.first()
    if app_data is None:
        raise RuntimeError(
            f"Security violation: required application role '{FORBIDDEN_APP_ROLE}' does not exist"
        )

    if any(flag is None or not isinstance(flag, bool) for flag in app_data):
        raise RuntimeError(
            f"Security violation: role '{FORBIDDEN_APP_ROLE}' has null or non-boolean attributes"
        )

    elevated_flags = app_data[1:]
    if any(flag is not False for flag in elevated_flags):
        raise RuntimeError(
            f"Security violation: role '{FORBIDDEN_APP_ROLE}' possesses elevated attributes"
        )

    app_db_create = await connection.scalar(
        text("SELECT has_database_privilege(:app_role, current_database(), 'CREATE')"),
        {"app_role": FORBIDDEN_APP_ROLE},
    )
    if app_db_create is not False:
        raise RuntimeError(
            f"Security violation: role '{FORBIDDEN_APP_ROLE}' has CREATE privilege on database "
            f"(or unknown privilege state: {app_db_create})"
        )

    app_sch_create = await connection.scalar(
        text("SELECT has_schema_privilege(:app_role, 'public', 'CREATE')"),
        {"app_role": FORBIDDEN_APP_ROLE},
    )
    if app_sch_create is not False:
        raise RuntimeError(
            f"Security violation: role '{FORBIDDEN_APP_ROLE}' "
            f"has CREATE privilege on public schema (or unknown privilege state: {app_sch_create})"
        )

    app_memberships = await connection.scalar(
        text(
            "SELECT EXISTS (SELECT 1 FROM pg_auth_members am "
            "JOIN pg_roles r ON am.member = r.oid WHERE r.rolname = :app_role)"
        ),
        {"app_role": FORBIDDEN_APP_ROLE},
    )
    if app_memberships is not False:
        raise RuntimeError(
            f"Security violation: role '{FORBIDDEN_APP_ROLE}' has elevated role memberships "
            f"(or unknown membership state: {app_memberships})"
        )

    owned_tbls = await connection.scalar(
        text(
            "SELECT count(*) FROM pg_tables WHERE schemaname = 'public' AND tableowner = :app_role"
        ),
        {"app_role": FORBIDDEN_APP_ROLE},
    )
    if owned_tbls is None or owned_tbls != 0:
        raise RuntimeError(
            f"Security violation: role '{FORBIDDEN_APP_ROLE}' owns tables in public schema"
        )

    owned_vws = await connection.scalar(
        text("SELECT count(*) FROM pg_views WHERE schemaname = 'public' AND viewowner = :app_role"),
        {"app_role": FORBIDDEN_APP_ROLE},
    )
    if owned_vws is None or owned_vws != 0:
        raise RuntimeError(
            f"Security violation: role '{FORBIDDEN_APP_ROLE}' owns views in public schema"
        )

    owned_nsps = await connection.scalar(
        text(
            "SELECT count(*) FROM pg_namespace "
            "WHERE nspowner = (SELECT oid FROM pg_roles WHERE rolname = :app_role)"
        ),
        {"app_role": FORBIDDEN_APP_ROLE},
    )
    if owned_nsps is None or owned_nsps != 0:
        raise RuntimeError(
            f"Security violation: role '{FORBIDDEN_APP_ROLE}' owns database namespaces"
        )

    # Finding 3: Complete strict empty schema baseline verification
    user_tables_count = await connection.scalar(
        text(
            "SELECT count(*) FROM information_schema.tables WHERE "
            "table_schema = 'public' AND table_name != 'alembic_version'"
        )
    )
    if user_tables_count is None or user_tables_count != 0:
        raise RuntimeError(
            f"Staging database contains {user_tables_count} existing tables in schema public; "
            "expected empty baseline for Gate 3"
        )

    alembic_table_exists = await connection.scalar(
        text(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE "
            "table_schema = 'public' AND table_name = 'alembic_version')"
        )
    )
    if alembic_table_exists is not False and alembic_table_exists is not True:
        raise RuntimeError("Security violation: unexpected catalog result for alembic_table_exists")

    if alembic_table_exists is True:
        existing_rev = await connection.scalar(
            text("SELECT version_num FROM alembic_version LIMIT 1")
        )
        raise RuntimeError(
            f"Staging database already contains 'alembic_version' table "
            f"(revision: '{existing_rev}'); expected clean unmigrated baseline for Gate 3"
        )

    views_count = await connection.scalar(
        text("SELECT count(*) FROM information_schema.views WHERE table_schema = 'public'")
    )
    if views_count is None or views_count != 0:
        raise RuntimeError(
            f"Staging database contains {views_count} existing views in schema public; "
            "expected empty baseline for Gate 3"
        )

    matviews_count = await connection.scalar(
        text("SELECT count(*) FROM pg_matviews WHERE schemaname = 'public'")
    )
    if matviews_count is None or matviews_count != 0:
        raise RuntimeError(
            f"Staging database contains {matviews_count} existing materialized views in public; "
            "expected empty baseline for Gate 3"
        )

    sequences_count = await connection.scalar(
        text("SELECT count(*) FROM information_schema.sequences WHERE sequence_schema = 'public'")
    )
    if sequences_count is None or sequences_count != 0:
        raise RuntimeError(
            f"Staging database contains {sequences_count} existing sequences in schema public; "
            "expected empty baseline for Gate 3"
        )

    functions_count = await connection.scalar(
        text(
            "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON p.pronamespace = n.oid "
            "WHERE n.nspname = 'public'"
        )
    )
    if functions_count is None or functions_count != 0:
        raise RuntimeError(
            f"Staging database contains {functions_count} existing functions in schema public; "
            "expected empty baseline for Gate 3"
        )

    # Finding 3: Domains, ranges, multiranges, enums, and composite types in schema public
    types_count = await connection.scalar(
        text(
            "SELECT count(*) FROM pg_type t JOIN pg_namespace n ON t.typnamespace = n.oid "
            "WHERE n.nspname = 'public' AND t.typtype IN ('c', 'e', 'd', 'r', 'm')"
        )
    )
    if types_count is None or types_count != 0:
        raise RuntimeError(
            f"Staging database contains {types_count} existing custom types/domains/ranges "
            "in schema public; expected empty baseline for Gate 3"
        )

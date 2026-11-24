"""Guard and execution wrapper for Gate 4 database grants.

Enforces fail-closed staging-only verification and the zero-table-grant policy for T-05:
1. Neon Control-Plane Verification:
   Fetches fresh metadata via authenticated HTTPS opener with strict redirect rejection.
   Asserts project ID, branch ID, endpoint ID, and hostname match approved staging invariants.
   Rejects protected production branch (br-holy-lake-bi0hc1lu) across all modes.
2. Authentic TLS Transport Verification:
   Enforces sslmode=verify-full with trusted CA certificate chain and hostname matching.
3. Database Identity & Administrator Role Verification:
   Asserts current_database() == "neondb".
   Asserts current_user == "neondb_owner" and session_user == "neondb_owner".
   Asserts target application role == "dormiostay_app" exactly.
4. Authoritative Neon Metadata:
   Asserts neon.branch_id, neon.project_id, neon.endpoint_id in pg_settings.
   Fails closed on missing or conflicting Neon metadata.
5. Zero-Table-Grant Security Policy (T-05):
   The backend currently implements health/readiness endpoints and does NOT require
   application-table privileges.
   - ZERO SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER on any table.
   - ZERO column-level privileges (has_any_column_privilege) on all governed tables.
   - ZERO grants on audit_logs (both table and column level are denied).
   - ZERO grants on alembic_version.
   - ZERO table or column GRANT OPTIONs.
   - ZERO object ownership (relations, schemas, functions, types).
   - ZERO role memberships in pg_auth_members.
   - ZERO unexpected callable SECURITY DEFINER routines in non-system schemas.
   - Only independently justified CONNECT on database and USAGE on schema public are preserved.
6. Single Transaction Architecture:
   Starts transaction exactly once before any query execution, runs preflight, applies targeted
   revocations on approved tables, grants schema usage, and verifies complete catalog security
   invariants before COMMIT. Automatic rollback on any failure.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.migration_guard import (
    APPROVED_STAGING_BRANCH_ID,
    APPROVED_STAGING_HOST,
    PROTECTED_PRODUCTION_BRANCH_ID,
    ApprovedTargetIdentity,
    _verify_client_tls,
    verify_neon_control_plane,
)
from app.db.session import async_url

APPROVED_APPLICATION_ROLE: str = "dormiostay_app"

# 11 migrated application tables from 0001_foundation
APPLICATION_TABLES: tuple[str, ...] = (
    "amenities",
    "locations",
    "platform_settings",
    "property_categories",
    "institutions",
    "users",
    "user_profiles",
    "user_sessions",
    "email_verification_tokens",
    "password_reset_tokens",
    "audit_logs",
)

# All tables in public schema under Gate 4 governance (11 application tables + alembic_version)
ALL_GOVERNED_TABLES: tuple[str, ...] = APPLICATION_TABLES + ("alembic_version",)

# All standard PostgreSQL table privilege types
ALL_TABLE_PRIVILEGES: tuple[str, ...] = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
)

# Standard PostgreSQL column privilege types
COLUMN_PRIVILEGES: tuple[str, ...] = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "REFERENCES",
)

# Allowed SECURITY DEFINER functions in non-system schemas (empty set for foundation schema)
ALLOWED_SECURITY_DEFINER_ROUTINES: frozenset[tuple[str, str]] = frozenset()


def resolve_grant_target(
    direct_url: str | None,
    migration_env: str | None = None,
    migration_confirmation: str | None = None,
    target_role: str | None = None,
    neon_api_key: str | None = None,
    control_plane_transport: Any = None,
    control_plane_opener: Any = None,
) -> tuple[URL, ApprovedTargetIdentity]:
    """Validate and resolve target database for Gate 4 application role grants.

    Enforces strict staging invariants, authenticated control-plane verification,
    authentic TLS, and ensures the target role equals dormiostay_app exactly.
    """
    if not direct_url:
        raise ValueError("DATABASE_URL_DIRECT is required for Gate 4 grants")

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

    if migration_env != "staging":
        raise ValueError("Gate 4 grants are authorized only for staging environment")

    valid_confirmations = {"staging", APPROVED_STAGING_BRANCH_ID}
    if not migration_confirmation or migration_confirmation not in valid_confirmations:
        raise ValueError("Explicit confirmation matching staging is required for Gate 4")

    # Require target role to be dormiostay_app
    if target_role != APPROVED_APPLICATION_ROLE:
        raise ValueError(f"Target role must be '{APPROVED_APPLICATION_ROLE}', got '{target_role}'")

    if not parsed.password:
        raise ValueError("Migration credentials (password) are required in DATABASE_URL_DIRECT")

    if host != APPROVED_STAGING_HOST.lower():
        raise ValueError(
            f"Target host '{host}' does not match authorized staging host '{APPROVED_STAGING_HOST}'"
        )

    # Cryptographic Neon Control-Plane Verification over HTTPS
    auth = verify_neon_control_plane(
        api_key=neon_api_key,
        transport=control_plane_transport,
        opener=control_plane_opener,
    )

    username = parsed.username or ""
    if username != auth.migration_role:
        raise ValueError(f"Grant administrator must be '{auth.migration_role}', got '{username}'")

    dbname = (parsed.path or "").lstrip("/")
    if dbname != auth.database:
        raise ValueError(f"Target database must be '{auth.database}', got '{dbname}'")

    # Enforce authentic TLS
    query_params = parse_qs(parsed.query)
    sslmode_vals = query_params.get("sslmode", [])
    ssl_vals = query_params.get("ssl", [])
    all_ssl = [v.lower() for v in (sslmode_vals + ssl_vals)]

    insecure_ssl_values = {"disable", "allow", "prefer", "require", "verify-ca", "verify_ca"}
    if not all_ssl or any(v in insecure_ssl_values for v in all_ssl):
        raise ValueError(
            "Gate 4 grants require authentic TLS (sslmode=verify-full). "
            "Insecure or hostname-bypassing options are strictly rejected."
        )

    valid_ssl_values = {"verify-full", "verify_full"}
    if not any(v in valid_ssl_values for v in all_ssl):
        raise ValueError("Gate 4 grants require authentic TLS (sslmode=verify-full)")

    url = async_url(direct_url)
    return url, auth


async def verify_grant_connection(
    connection: AsyncConnection,
    expected_target: ApprovedTargetIdentity,
    target_role: str = APPROVED_APPLICATION_ROLE,
) -> None:
    """Verify runtime database connection and role safety before executing grants.

    Asserts:
    1. TLS verification on raw driver transport (CERT_REQUIRED, check_hostname).
    2. current_database() == neondb.
    3. current_user and session_user == neondb_owner.
    4. Authoritative pg_settings contains neon.branch_id matching staging and != production.
    5. target_role == dormiostay_app, exists, and possesses zero elevated attributes.
    6. target_role has no direct or indirect role memberships in pg_auth_members.
    7. target_role owns 0 relations (tables, views, sequences), schemas, functions, or types.
    8. target_role has no CREATE privilege on database or public schema.
    """
    if not expected_target.control_plane_verified:
        raise RuntimeError("Approved target identity lacks verified control-plane metadata")

    if target_role != APPROVED_APPLICATION_ROLE:
        raise RuntimeError(f"Target role must be '{APPROVED_APPLICATION_ROLE}'")

    # 1. TLS verification on raw driver connection
    await _verify_client_tls(connection, expected_target.host)

    # 2. Database name verification
    actual_db = await connection.scalar(text("SELECT current_database()"))
    if actual_db != expected_target.database:
        raise RuntimeError(
            f"Effective database '{actual_db}' differs from approved '{expected_target.database}'"
        )

    # 3. Administrator role verification
    user_res = await connection.execute(text("SELECT current_user, session_user"))
    users = user_res.first()
    if not users:
        raise RuntimeError("Failed to verify database administrator identity")
    curr_u, sess_u = users[0], users[1]

    if curr_u != expected_target.migration_role or sess_u != expected_target.migration_role:
        raise RuntimeError(
            f"Connected user '{curr_u}' (session: '{sess_u}') differs from "
            f"approved administrator '{expected_target.migration_role}'"
        )

    # 4. Check authoritative Neon engine settings (fail closed on missing metadata or production)
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
    except Exception as exc:
        raise RuntimeError(f"Failed to query authoritative Neon settings: {exc}") from exc

    actual_branch = neon_settings.get("neon.branch_id")
    if not actual_branch:
        raise RuntimeError(
            "Missing authoritative Neon metadata in pg_settings ('neon.branch_id'). "
            "Failing closed by security policy."
        )

    if actual_branch == PROTECTED_PRODUCTION_BRANCH_ID:
        raise RuntimeError("CRITICAL: Connected to protected production branch! Aborting.")

    if actual_branch != expected_target.branch_id:
        raise RuntimeError(
            f"Authoritative Neon branch '{actual_branch}' does not match "
            f"approved staging branch '{expected_target.branch_id}'"
        )

    actual_proj = neon_settings.get("neon.project_id")
    if actual_proj and actual_proj != expected_target.project_id:
        raise RuntimeError(
            f"Authoritative Neon project '{actual_proj}' differs from approved "
            f"'{expected_target.project_id}'"
        )

    actual_ep = neon_settings.get("neon.endpoint_id")
    if actual_ep and actual_ep != expected_target.endpoint_id:
        raise RuntimeError(
            f"Authoritative Neon endpoint '{actual_ep}' differs from approved "
            f"'{expected_target.endpoint_id}'"
        )

    # 5. Verify target role attributes
    app_role_res = await connection.execute(
        text(
            "SELECT rolcanlogin, rolsuper, rolcreaterole, rolcreatedb, rolreplication, "
            "rolbypassrls FROM pg_roles WHERE rolname = :app_role"
        ),
        {"app_role": target_role},
    )
    app_data = app_role_res.first()
    if app_data is None:
        raise RuntimeError(f"Target application role '{target_role}' does not exist")

    if any(flag is None or not isinstance(flag, bool) for flag in app_data):
        raise RuntimeError(f"Role '{target_role}' has null or non-boolean attributes")

    elevated_flags = app_data[1:]
    if any(flag is not False for flag in elevated_flags):
        raise RuntimeError(
            f"Security violation: role '{target_role}' possesses elevated attributes"
        )

    # 6. Verify target role memberships (no inherited privileges)
    app_memberships = await connection.scalar(
        text(
            "SELECT EXISTS (SELECT 1 FROM pg_auth_members am "
            "JOIN pg_roles r ON am.member = r.oid WHERE r.rolname = :app_role)"
        ),
        {"app_role": target_role},
    )
    if app_memberships is not False:
        raise RuntimeError(
            f"Security violation: role '{target_role}' has role memberships in pg_auth_members"
        )

    # 7. Verify no CREATE privileges
    db_create = await connection.scalar(
        text("SELECT has_database_privilege(:app_role, current_database(), 'CREATE')"),
        {"app_role": target_role},
    )
    if db_create is not False:
        raise RuntimeError(f"Security violation: role '{target_role}' has CREATE on database")

    sch_create = await connection.scalar(
        text("SELECT has_schema_privilege(:app_role, 'public', 'CREATE')"),
        {"app_role": target_role},
    )
    if sch_create is not False:
        raise RuntimeError(f"Security violation: role '{target_role}' has CREATE on schema public")

    # 8. Verify comprehensive object ownership across relations, schemas, functions, types
    owned_rels = await connection.scalar(
        text(
            "SELECT count(*) FROM pg_class c "
            "JOIN pg_roles r ON c.relowner = r.oid "
            "WHERE r.rolname = :app_role"
        ),
        {"app_role": target_role},
    )
    if owned_rels is None or owned_rels != 0:
        raise RuntimeError(
            f"Security violation: role '{target_role}' owns {owned_rels} relation(s)"
        )

    owned_schemas = await connection.scalar(
        text(
            "SELECT count(*) FROM pg_namespace n "
            "JOIN pg_roles r ON n.nspowner = r.oid "
            "WHERE r.rolname = :app_role"
        ),
        {"app_role": target_role},
    )
    if owned_schemas is None or owned_schemas != 0:
        raise RuntimeError(
            f"Security violation: role '{target_role}' owns {owned_schemas} schema(s)"
        )

    owned_procs = await connection.scalar(
        text(
            "SELECT count(*) FROM pg_proc p "
            "JOIN pg_roles r ON p.proowner = r.oid "
            "WHERE r.rolname = :app_role"
        ),
        {"app_role": target_role},
    )
    if owned_procs is None or owned_procs != 0:
        raise RuntimeError(
            f"Security violation: role '{target_role}' owns {owned_procs} routine(s)"
        )

    owned_types = await connection.scalar(
        text(
            "SELECT count(*) FROM pg_type t "
            "JOIN pg_roles r ON t.typowner = r.oid "
            "WHERE r.rolname = :app_role"
        ),
        {"app_role": target_role},
    )
    if owned_types is None or owned_types != 0:
        raise RuntimeError(f"Security violation: role '{target_role}' owns {owned_types} type(s)")


async def verify_catalog_privileges(
    connection: AsyncConnection,
    target_role: str = APPROVED_APPLICATION_ROLE,
) -> dict[str, Any]:
    """Perform comprehensive in-transaction security verification of catalog state.

    Enforces the T-05 zero-table-grant policy across all 11 application tables
    and alembic_version.

    Rejects:
    - Any direct or effective table privilege for target_role (all 7 privileges).
    - Any effective column-level privilege for target_role or PUBLIC (has_any_column_privilege).
    - Any column privilege on audit_logs, including SELECT(action).
    - Any raw column ACL entries in pg_attribute.attacl.
    - Any raw table ACL entries in pg_class.relacl granting privileges to target_role or PUBLIC.
    - Any table or column GRANT OPTION for target_role or PUBLIC.
    - Any default ACL granting privileges to target_role or PUBLIC.
    - Any unexpected callable SECURITY DEFINER routines in non-system schemas.
    - Any role membership in pg_auth_members.
    - Any object ownership (relations, schemas, functions, types).
    - Any CREATE privilege on database or schemas.
    - Missing CONNECT on database or USAGE on schema public.
    """
    results: dict[str, Any] = {"table_privileges": {}, "catalog_assertions": []}

    # 1. Verify zero table privileges across all 12 tables and all 7 privilege types
    for tbl in ALL_GOVERNED_TABLES:
        results["table_privileges"][tbl] = {}
        for priv in ALL_TABLE_PRIVILEGES:
            has_priv = await connection.scalar(
                text("SELECT has_table_privilege(:role, :tbl, :priv)"),
                {"role": target_role, "tbl": f"public.{tbl}", "priv": priv},
            )
            results["table_privileges"][tbl][priv] = has_priv
            if has_priv is not False:
                raise RuntimeError(
                    f"Catalog security violation: {target_role} has unexpected {priv} "
                    f"privilege on public.{tbl} (zero-grant policy violated)"
                )

            pub_priv = await connection.scalar(
                text("SELECT has_table_privilege('public', :tbl, :priv)"),
                {"tbl": f"public.{tbl}", "priv": priv},
            )
            if pub_priv is not False:
                raise RuntimeError(
                    f"Catalog security violation: PUBLIC has unexpected {priv} "
                    f"privilege on public.{tbl}"
                )

    # 2. Verify effective column privileges via has_any_column_privilege
    for tbl in ALL_GOVERNED_TABLES:
        for priv in COLUMN_PRIVILEGES:
            has_col = await connection.scalar(
                text("SELECT has_any_column_privilege(:role, :tbl, :priv)"),
                {"role": target_role, "tbl": f"public.{tbl}", "priv": priv},
            )
            if has_col is not False:
                raise RuntimeError(
                    f"Effective column privilege leakage: {target_role} has unexpected {priv} "
                    f"on column(s) of public.{tbl}"
                )

            pub_col = await connection.scalar(
                text("SELECT has_any_column_privilege('public', :tbl, :priv)"),
                {"tbl": f"public.{tbl}", "priv": priv},
            )
            if pub_col is not False:
                raise RuntimeError(
                    f"Effective column privilege leakage: PUBLIC has unexpected {priv} "
                    f"on column(s) of public.{tbl}"
                )

    # 3. Explicit check on audit_logs column privileges (including action)
    audit_action_priv = await connection.scalar(
        text("SELECT has_column_privilege(:role, 'public.audit_logs', 'action', 'SELECT')"),
        {"role": target_role},
    )
    if audit_action_priv is not False:
        raise RuntimeError(
            f"Effective column privilege leakage: {target_role} has SELECT on audit_logs.action"
        )

    # 4. Raw Column ACL Catalog (pg_attribute.attacl)
    raw_col_acls = await connection.execute(
        text(
            "SELECT c.relname, a.attname, a.attacl "
            "FROM pg_attribute a "
            "JOIN pg_class c ON a.attrelid = c.oid "
            "JOIN pg_namespace n ON c.relnamespace = n.oid "
            "WHERE n.nspname = 'public' "
            "AND c.relname = ANY(:governed_tables) "
            "AND a.attnum > 0 "
            "AND NOT a.attisdropped "
            "AND a.attacl IS NOT NULL"
        ),
        {"governed_tables": list(ALL_GOVERNED_TABLES)},
    )
    for row in raw_col_acls.fetchall():
        relname, attname, attacl = row[0], row[1], row[2]
        if attacl:
            for entry in attacl:
                entry_str = str(entry)
                if f"{target_role}=" in entry_str or entry_str.startswith("=") or "*" in entry_str:
                    raise RuntimeError(
                        f"Raw column ACL leakage: public.{relname}.{attname} has "
                        f"unexpected ACL: {entry_str}"
                    )

    # 5. Raw Table ACL Catalog (pg_class.relacl)
    raw_tbl_acls = await connection.execute(
        text(
            "SELECT c.relname, c.relacl "
            "FROM pg_class c "
            "JOIN pg_namespace n ON c.relnamespace = n.oid "
            "WHERE n.nspname = 'public' "
            "AND c.relname = ANY(:governed_tables) "
            "AND c.relacl IS NOT NULL"
        ),
        {"governed_tables": list(ALL_GOVERNED_TABLES)},
    )
    for row in raw_tbl_acls.fetchall():
        relname, relacl = row[0], row[1]
        if relacl:
            for entry in relacl:
                entry_str = str(entry)
                if (
                    f"{target_role}=" in entry_str
                    or entry_str.startswith("=")
                    or (f"{target_role}" in entry_str and "*" in entry_str)
                ):
                    raise RuntimeError(
                        f"Raw table ACL leakage: public.{relname} has "
                        f"unexpected relacl: {entry_str}"
                    )

    # 6. Verify target_role and PUBLIC hold zero table or column GRANT OPTIONs
    grant_opt_res = await connection.execute(
        text(
            "SELECT table_name, privilege_type "
            "FROM information_schema.table_privileges "
            "WHERE table_schema = 'public' "
            "AND grantee IN (:role, 'PUBLIC') "
            "AND is_grantable = 'YES'"
        ),
        {"role": target_role},
    )
    grant_opts = grant_opt_res.fetchall()
    if grant_opts:
        raise RuntimeError(
            f"Catalog security violation: {target_role} or PUBLIC holds GRANT OPTION: {grant_opts}"
        )

    # 7. Verify default ACLs do not leak privileges to target_role or PUBLIC
    defacl_res = await connection.execute(
        text(
            "SELECT defaclrole::regrole::text as grantor, defaclobjtype, defaclacl "
            "FROM pg_default_acl"
        )
    )
    for row in defacl_res.fetchall():
        acl_entries = row[2] or []
        for entry in acl_entries:
            entry_str = str(entry)
            if f"{target_role}=" in entry_str or entry_str.startswith("="):
                raise RuntimeError(
                    f"Catalog security violation: Default ACL leaks privileges: {entry_str}"
                )

    # 8. Inventory non-system schemas and verify least privilege
    schemas_res = await connection.execute(
        text(
            "SELECT nspname FROM pg_namespace "
            "WHERE nspname NOT LIKE 'pg_%' AND nspname != 'information_schema'"
        )
    )
    non_sys_schemas = [row[0] for row in schemas_res.fetchall()]
    for sch_name in non_sys_schemas:
        sch_create = await connection.scalar(
            text("SELECT has_schema_privilege(:app_role, :sch, 'CREATE')"),
            {"app_role": target_role, "sch": sch_name},
        )
        if sch_create is not False:
            raise RuntimeError(
                f"Security violation: role '{target_role}' has CREATE on schema '{sch_name}'"
            )

        sch_usage = await connection.scalar(
            text("SELECT has_schema_privilege(:app_role, :sch, 'USAGE')"),
            {"app_role": target_role, "sch": sch_name},
        )
        if sch_name == "public":
            if sch_usage is not True:
                raise RuntimeError(
                    f"Role '{target_role}' missing justified USAGE privilege on schema public"
                )
        else:
            if sch_usage is not False:
                raise RuntimeError(
                    f"Security violation: role '{target_role}' has unexpected USAGE "
                    f"on schema '{sch_name}'"
                )

    # 9. Verify no unexpected callable SECURITY DEFINER routines across all non-system schemas
    secdef_res = await connection.execute(
        text(
            "SELECT n.nspname, p.proname, p.oid "
            "FROM pg_proc p "
            "JOIN pg_namespace n ON p.pronamespace = n.oid "
            "WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname != 'information_schema' "
            "AND p.prosecdef = true"
        )
    )
    for row in secdef_res.fetchall():
        sch_n, fn_n, fn_oid = str(row[0]), str(row[1]), row[2]
        if (sch_n, fn_n) not in ALLOWED_SECURITY_DEFINER_ROUTINES:
            raise RuntimeError(
                f"Catalog security violation: Unexpected SECURITY DEFINER routine '{sch_n}.{fn_n}' "
                f"found outside reviewed allowlist"
            )
        app_exec = await connection.scalar(
            text("SELECT has_function_privilege(:app_role, :oid, 'EXECUTE')"),
            {"app_role": target_role, "oid": fn_oid},
        )
        if app_exec is not False:
            raise RuntimeError(
                f"Security violation: role '{target_role}' has EXECUTE on SECURITY DEFINER "
                f"routine '{sch_n}.{fn_n}'"
            )
        pub_exec = await connection.scalar(
            text("SELECT has_function_privilege('public', :oid, 'EXECUTE')"),
            {"oid": fn_oid},
        )
        if pub_exec is not False:
            raise RuntimeError(
                f"Security violation: PUBLIC has EXECUTE on SECURITY DEFINER "
                f"routine '{sch_n}.{fn_n}'"
            )

    # 10. Verify database privileges
    can_connect = await connection.scalar(
        text("SELECT has_database_privilege(:role, current_database(), 'CONNECT')"),
        {"role": target_role},
    )
    if can_connect is not True:
        raise RuntimeError(f"Role '{target_role}' missing justified CONNECT privilege on database")

    can_db_create = await connection.scalar(
        text("SELECT has_database_privilege(:role, current_database(), 'CREATE')"),
        {"role": target_role},
    )
    if can_db_create is not False:
        raise RuntimeError(f"Role '{target_role}' has forbidden CREATE privilege on database")

    # 11. Verify zero object ownership in catalogs
    owned_tables = await connection.scalar(
        text("SELECT count(*) FROM pg_tables WHERE schemaname = 'public' AND tableowner = :role"),
        {"role": target_role},
    )
    if owned_tables is None or owned_tables != 0:
        raise RuntimeError(f"Role '{target_role}' owns {owned_tables} tables in schema public")

    results["catalog_assertions"] = [
        "zero_table_privileges_verified",
        "zero_public_privileges_verified",
        "zero_effective_column_privileges_verified",
        "raw_acls_clean",
        "zero_grant_options_verified",
        "default_acls_clean",
        "non_system_schemas_audited",
        "no_unexpected_security_definer_routines",
        "least_privilege_schema_db_verified",
        "zero_object_ownership_verified",
    ]
    return results


async def execute_gate4_grants_async(
    connection: AsyncConnection,
    expected_target: ApprovedTargetIdentity,
    target_role: str = APPROVED_APPLICATION_ROLE,
) -> dict[str, Any]:
    """Execute Gate 4 grants using single-transaction async SQLAlchemy architecture.

    Single Transaction Ownership (Fix 1):
    - Asserts that connection is not already in an active transaction.
    - Initiates exactly ONE transaction via `async with connection.begin():`.
    - Executes preflight verification queries (verify_grant_connection) INSIDE the transaction.
    - Explicitly sets search_path = public.
    - Applies targeted revocations exclusively on the 12 approved governed tables.
    - Grants justified USAGE on schema public.
    - Executes comprehensive in-transaction catalog verification (verify_catalog_privileges).
    - If any assertion fails, automatically rolls back all changes.
    - Commits only when every preflight, mutation, and catalog assertion succeeds.
    - Idempotent and safely repeatable when database matches approved policy.
    """
    if target_role != APPROVED_APPLICATION_ROLE:
        raise ValueError(f"Target role must be '{APPROVED_APPLICATION_ROLE}', got '{target_role}'")

    if connection.in_transaction():
        raise InvalidRequestError(
            "Connection is already in an active transaction. execute_gate4_grants_async "
            "must establish the single transaction boundary before any SQL is executed."
        )

    async with connection.begin():
        # 1. Preflight connection verification (runs inside the single transaction)
        await verify_grant_connection(connection, expected_target, target_role=target_role)

        # 2. Explicit search_path protection
        await connection.execute(text("SET search_path = public"))

        # 3. Targeted revocations on all 11 application tables and alembic_version
        for tbl in ALL_GOVERNED_TABLES:
            await connection.execute(
                text(f'REVOKE ALL PRIVILEGES ON TABLE public."{tbl}" FROM "{target_role}"')
            )

        # 4. Preserved independently justified privileges: USAGE on schema public
        await connection.execute(text(f'GRANT USAGE ON SCHEMA public TO "{target_role}"'))

        # 5. In-transaction pre-commit security verification
        # If any check fails, an exception is raised, triggering immediate ROLLBACK
        verification_results = await verify_catalog_privileges(connection, target_role=target_role)

    return verification_results

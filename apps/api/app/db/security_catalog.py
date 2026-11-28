"""Exact read-only catalog comparison. Manifests are reviewed release inputs."""

import json
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

FOUNDATION = "0001_foundation"
DESTINATION = "0003_row_level_security"
MANIFEST_DIR = Path(__file__).with_name("security_manifests")
# Only application namespaces. Inspect all of them to catch unexpected accessible objects.
USER_SCHEMA = "left(n.nspname,3) <> 'pg_' AND n.nspname <> 'information_schema'"
RELATIONS = "pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace"
APP = "'dormiostay_app'"
QUERIES = {
    "namespaces": f"""SELECT nspname, nspowner::regrole::text,
        has_schema_privilege({APP}, n.oid,'CREATE'),has_schema_privilege({APP}, n.oid,'USAGE'),
        nspacl::text FROM pg_namespace n WHERE {USER_SCHEMA}""",
    "database": f"""SELECT datdba::regrole::text,has_database_privilege({APP},oid,'CREATE'),
        has_database_privilege({APP},oid,'CONNECT'),has_database_privilege({APP},oid,'TEMP'),
        datacl::text FROM pg_database WHERE datname=current_database()""",
    "role": f"""SELECT rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolreplication,
        rolbypassrls,rolinherit,rolconfig FROM pg_roles WHERE rolname={APP}""",
    "memberships": f"""SELECT roleid::regrole::text FROM pg_auth_members
        WHERE member={APP}::regrole OR roleid={APP}::regrole""",
    "defaults": """SELECT defaclrole::regrole::text, n.nspname, defaclobjtype::text,
        defaclacl::text FROM pg_default_acl d
        LEFT JOIN pg_namespace n ON n.oid=d.defaclnamespace""",
    "settings": f"""SELECT setrole::regrole::text,setconfig FROM pg_db_role_setting
        WHERE setdatabase IN (0,(SELECT oid FROM pg_database WHERE datname=current_database()))
        AND setrole IN (0,{APP}::regrole,'neondb_owner'::regrole)""",
    "relations": f"""SELECT n.nspname,c.relname,c.relkind::text,c.relowner::regrole::text,
        c.relrowsecurity,c.relforcerowsecurity,c.relacl::text,c.reloptions
        FROM {RELATIONS} WHERE {USER_SCHEMA}""",
    "columns": f"""SELECT c.relname,a.attname,a.attnum,format_type(a.atttypid,a.atttypmod),
        a.attnotnull,a.attidentity::text,a.attgenerated::text,a.attacl::text,
        pg_get_expr(d.adbin,d.adrelid),a.attcollation::regcollation::text
        FROM {RELATIONS} JOIN pg_attribute a ON a.attrelid=c.oid
        LEFT JOIN pg_attrdef d ON (d.adrelid,d.adnum)=(a.attrelid,a.attnum)
        WHERE {USER_SCHEMA} AND c.relkind='r' AND a.attnum>0 AND NOT a.attisdropped""",
    "constraints": f"""SELECT c.relname,k.conname,pg_get_constraintdef(k.oid),k.convalidated
        FROM {RELATIONS} JOIN pg_constraint k ON k.conrelid=c.oid
        WHERE {USER_SCHEMA} AND k.contype <> 'n'""",
    "indexes": f"""SELECT c.relname,pg_get_indexdef(i.indexrelid),i.indisvalid,i.indisready
        FROM {RELATIONS} JOIN pg_index i ON i.indrelid=c.oid WHERE {USER_SCHEMA}""",
    "policies": f"""SELECT c.relname,p.polname,p.polcmd::text,p.polpermissive,
        ARRAY(SELECT CASE WHEN r=0 THEN 'PUBLIC' ELSE r::regrole::text END
        FROM unnest(p.polroles) r ORDER BY 1),pg_get_expr(p.polqual,c.oid),
        pg_get_expr(p.polwithcheck,c.oid)
        FROM {RELATIONS} JOIN pg_policy p ON p.polrelid=c.oid WHERE {USER_SCHEMA}""",
    "triggers": f"""SELECT c.relname,t.tgname,t.tgenabled::text,pg_get_triggerdef(t.oid)
        FROM {RELATIONS} JOIN pg_trigger t ON t.tgrelid=c.oid
        WHERE {USER_SCHEMA} AND NOT t.tgisinternal""",
    "internal_triggers": f"""SELECT c.relname,k.conname,t.tgenabled::text,
        t.tgfoid::regprocedure::text FROM {RELATIONS} JOIN pg_trigger t ON t.tgrelid=c.oid
        LEFT JOIN pg_constraint k ON k.oid=t.tgconstraint
        WHERE {USER_SCHEMA} AND t.tgisinternal""",
    "routines": f"""SELECT n.nspname,p.oid::regprocedure::text,p.proowner::regrole::text,
        p.prosecdef,p.proacl::text,pg_get_functiondef(p.oid)
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE {USER_SCHEMA}""",
    "extensions": """SELECT extname,extversion,extnamespace::regnamespace::text,
        extowner::regrole::text FROM pg_extension""",
    "types": f"""SELECT n.nspname,t.typname,t.typtype::text,t.typowner::regrole::text,
        t.typacl::text FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
        WHERE {USER_SCHEMA}""",
    "foreign_servers": "SELECT srvname,srvowner::regrole::text,srvacl::text FROM pg_foreign_server",
    "system_table_acls": f"""SELECT n.nspname,c.relname,x.privilege_type,x.is_grantable
        FROM {RELATIONS} CROSS JOIN LATERAL aclexplode(c.relacl) x
        WHERE NOT ({USER_SCHEMA}) AND x.grantee={APP}::regrole""",
    "system_routine_acls": f"""SELECT n.nspname,p.oid::regprocedure::text,x.is_grantable
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        CROSS JOIN LATERAL aclexplode(p.proacl) x WHERE NOT ({USER_SCHEMA})
        AND x.grantee={APP}::regrole""",
    "role_ownership": f"""SELECT classid::regclass::text,deptype::text FROM pg_shdepend
        WHERE refclassid='pg_authid'::regclass AND refobjid={APP}::regrole AND deptype='o'""",
    "publications": "SELECT pubname,pubowner::regrole::text FROM pg_publication",
    "rules": f"""SELECT c.relname,r.rulename,pg_get_ruledef(r.oid) FROM {RELATIONS}
        JOIN pg_rewrite r ON r.ev_class=c.oid WHERE {USER_SCHEMA}""",
    "event_triggers": "SELECT evtname,evtevent,evtenabled::text FROM pg_event_trigger",
}


async def catalog_rows(connection: AsyncConnection) -> list[str]:
    """Return stable JSON lines. Caller pins the path so deparsing is reproducible."""
    version = await connection.scalar(
        text("SELECT pg_catalog.current_setting('server_version_num')")
    )
    if not isinstance(version, str) or not version.isdecimal() or int(version) // 10000 != 16:
        raise RuntimeError("Unreviewed PostgreSQL catalog version")
    if await connection.scalar(text("SELECT pg_catalog.current_schemas(true)")) != [
        "pg_catalog",
        "public",
    ]:
        raise RuntimeError("Unexpected catalog search path")
    rows = []
    for section, sql in QUERIES.items():
        result = await connection.execute(text(sql))
        for row in result:
            rows.append(json.dumps([section, *row], separators=(",", ":")))
    return sorted(rows)


def manifest(revision: str) -> list[str]:
    if revision not in (FOUNDATION, DESTINATION):
        raise ValueError("Unsupported catalog revision")
    rows = (MANIFEST_DIR / "foundation.jsonl").read_text().splitlines()
    if revision == DESTINATION:
        for line in (MANIFEST_DIR / "rls.delta").read_text().splitlines():
            if line.startswith("-"):
                rows.remove(line[1:])
            elif line.startswith("+"):
                rows.append(line[1:])
            else:
                raise ValueError("Invalid catalog delta")
    return sorted(rows)


async def verify_catalog(connection: AsyncConnection, revision: str) -> None:
    expected = manifest(revision)
    actual = await catalog_rows(connection)
    expected.remove(json.dumps(["revision", revision], separators=(",", ":")))
    if actual != expected:
        # Never include catalog expressions or user data in release logs.
        raise RuntimeError(f"Security catalog mismatch at {revision}")
    # Only touch application relations after ruling out a substituted view, policy,
    # trigger, or rule. This also requires an ordinary, correctly owned version table.
    version_rows = await connection.scalars(text("SELECT version_num FROM public.alembic_version"))
    if version_rows.all() != [revision]:
        raise RuntimeError(f"Security catalog mismatch at {revision}")

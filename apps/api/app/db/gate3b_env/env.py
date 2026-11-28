"""Shared-connection-only Alembic context for the disposable Gate 3b harness."""

from sqlalchemy import text
from sqlalchemy.engine import Connection

from alembic import context
from app.db.security_catalog import DESTINATION, FOUNDATION
from app.db.test_target import validate_test_url

connection = context.config.attributes.get("connection")
database = context.config.attributes.get("disposable_database")
if not isinstance(connection, Connection) or not isinstance(database, str):
    raise RuntimeError("Runner-owned disposable connection required")
validate_test_url(connection.engine.url.render_as_string(hide_password=True), database)
if context.is_offline_mode() or not connection.in_transaction():
    raise RuntimeError("Active online transaction required")
if context.get_revision_argument() != DESTINATION:
    raise RuntimeError("Unapproved destination")
if connection.scalars(text("SELECT version_num FROM public.alembic_version")).all() != [FOUNDATION]:
    raise RuntimeError("Unapproved starting revision")
if tuple(
    connection.execute(
        text(
            "SELECT current_database(),current_user,session_user,inet_server_addr() "
            "IN (inet '127.0.0.1',inet '::1')"
        )
    ).one()
) != (database, "neondb_owner", "neondb_owner", True):
    raise RuntimeError("Disposable session required")
context.configure(connection=connection, transactional_ddl=True, transaction_per_migration=False)
with context.begin_transaction():
    context.run_migrations()

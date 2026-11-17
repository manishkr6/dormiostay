"""Initial T-03 schema snapshot. No runtime model imports."""

from alembic import op

revision = "0001_foundation"
down_revision = None
branch_labels = None
depends_on = None

STATEMENTS = [
    """CREATE TABLE amenities (
    name TEXT NOT NULL,
    icon TEXT,
    is_active BOOLEAN DEFAULT true NOT NULL,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_amenities PRIMARY KEY (id),
    CONSTRAINT uq_amenities_name UNIQUE (name)
)""",
    """CREATE TABLE locations (
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    parent_id UUID,
    latitude FLOAT,
    longitude FLOAT,
    aliases TEXT[] DEFAULT '{}' NOT NULL,
    is_active BOOLEAN DEFAULT true NOT NULL,
    display_order INTEGER DEFAULT '0' NOT NULL,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_locations PRIMARY KEY (id),
    CONSTRAINT ck_locations_latitude CHECK (latitude BETWEEN -90 AND 90),
    CONSTRAINT ck_locations_longitude CHECK (longitude BETWEEN -180 AND 180),
    CONSTRAINT ck_locations_type CHECK
        (type IN ('country','state','district','city','neighbourhood')),
    CONSTRAINT ck_locations_parent CHECK (parent_id IS NULL OR parent_id <> id),
    CONSTRAINT fk_locations_parent_id_locations FOREIGN KEY(parent_id) REFERENCES locations (id)
)""",
    """CREATE TABLE platform_settings (
    key TEXT NOT NULL,
    value JSONB NOT NULL,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_platform_settings PRIMARY KEY (id),
    CONSTRAINT uq_platform_settings_key UNIQUE (key)
)""",
    """CREATE TABLE property_categories (
    name TEXT NOT NULL,
    is_active BOOLEAN DEFAULT true NOT NULL,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_property_categories PRIMARY KEY (id),
    CONSTRAINT uq_property_categories_name UNIQUE (name)
)""",
    """CREATE TABLE users (
    email TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT DEFAULT 'tenant' NOT NULL,
    status TEXT DEFAULT 'active' NOT NULL,
    email_verified_at TIMESTAMP WITH TIME ZONE,
    failed_login_count INTEGER DEFAULT '0' NOT NULL,
    locked_until TIMESTAMP WITH TIME ZONE,
    last_login_at TIMESTAMP WITH TIME ZONE,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_users PRIMARY KEY (id),
    CONSTRAINT ck_users_role CHECK (role IN ('tenant', 'owner', 'admin')),
    CONSTRAINT ck_users_status CHECK (status IN ('active', 'suspended', 'banned')),
    CONSTRAINT ck_users_failed_login_count CHECK (failed_login_count >= 0)
)""",
    """CREATE TABLE audit_logs (
    actor_id UUID,
    action TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id UUID,
    before JSONB,
    after JSONB,
    reason TEXT,
    ip INET,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_audit_logs PRIMARY KEY (id),
    CONSTRAINT fk_audit_logs_actor_id_users FOREIGN KEY(actor_id) REFERENCES users (id)
)""",
    """CREATE TABLE email_verification_tokens (
    user_id UUID NOT NULL,
    token_hash TEXT NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    used_at TIMESTAMP WITH TIME ZONE,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_email_verification_tokens PRIMARY KEY (id),
    CONSTRAINT ck_email_verification_tokens_token_hash CHECK (token_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT fk_email_verification_tokens_user_id_users
        FOREIGN KEY(user_id) REFERENCES users (id),
    CONSTRAINT uq_email_verification_tokens_token_hash UNIQUE (token_hash)
)""",
    """CREATE TABLE institutions (
    name TEXT NOT NULL,
    aliases TEXT[] DEFAULT '{}' NOT NULL,
    location_id UUID NOT NULL,
    latitude FLOAT,
    longitude FLOAT,
    is_active BOOLEAN DEFAULT true NOT NULL,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_institutions PRIMARY KEY (id),
    CONSTRAINT ck_institutions_latitude CHECK (latitude BETWEEN -90 AND 90),
    CONSTRAINT ck_institutions_longitude CHECK (longitude BETWEEN -180 AND 180),
    CONSTRAINT fk_institutions_location_id_locations
        FOREIGN KEY(location_id) REFERENCES locations (id)
)""",
    """CREATE TABLE password_reset_tokens (
    user_id UUID NOT NULL,
    token_hash TEXT NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    used_at TIMESTAMP WITH TIME ZONE,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_password_reset_tokens PRIMARY KEY (id),
    CONSTRAINT ck_password_reset_tokens_token_hash CHECK (token_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT fk_password_reset_tokens_user_id_users FOREIGN KEY(user_id) REFERENCES users (id),
    CONSTRAINT uq_password_reset_tokens_token_hash UNIQUE (token_hash)
)""",
    """CREATE TABLE user_sessions (
    user_id UUID NOT NULL,
    token_hash TEXT NOT NULL,
    last_activity_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    absolute_expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    revoked_at TIMESTAMP WITH TIME ZONE,
    revocation_reason TEXT,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_user_sessions PRIMARY KEY (id),
    CONSTRAINT ck_user_sessions_token_hash CHECK (token_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT fk_user_sessions_user_id_users FOREIGN KEY(user_id) REFERENCES users (id),
    CONSTRAINT uq_user_sessions_token_hash UNIQUE (token_hash)
)""",
    """CREATE TABLE user_profiles (
    user_id UUID NOT NULL,
    full_name TEXT NOT NULL,
    phone TEXT,
    avatar_url TEXT,
    gender TEXT,
    preferred_budget_min INTEGER,
    preferred_budget_max INTEGER,
    preferred_areas TEXT[] DEFAULT '{}' NOT NULL,
    institution_id UUID,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_user_profiles PRIMARY KEY (id),
    CONSTRAINT ck_user_profiles_budget_min CHECK (preferred_budget_min >= 0),
    CONSTRAINT ck_user_profiles_budget_range CHECK (preferred_budget_max >= preferred_budget_min),
    CONSTRAINT uq_user_profiles_user_id UNIQUE (user_id),
    CONSTRAINT fk_user_profiles_user_id_users FOREIGN KEY(user_id) REFERENCES users (id),
    CONSTRAINT fk_user_profiles_institution_id_institutions
        FOREIGN KEY(institution_id) REFERENCES institutions (id)
)""",
    """CREATE INDEX ix_locations_name_trgm ON locations USING gin (name gin_trgm_ops)""",
    """CREATE INDEX ix_locations_parent_id ON locations (parent_id)""",
    """CREATE UNIQUE INDEX uq_users_email_ci ON users (lower(email))""",
    """CREATE INDEX ix_audit_logs_actor_id ON audit_logs (actor_id)""",
    """CREATE INDEX ix_email_verification_tokens_expires_at
        ON email_verification_tokens (expires_at)""",
    """CREATE INDEX ix_email_verification_tokens_user_id ON email_verification_tokens (user_id)""",
    """CREATE INDEX ix_institutions_location_id ON institutions (location_id)""",
    """CREATE INDEX ix_password_reset_tokens_expires_at ON password_reset_tokens (expires_at)""",
    """CREATE INDEX ix_password_reset_tokens_user_id ON password_reset_tokens (user_id)""",
    """CREATE INDEX ix_user_sessions_expires_at ON user_sessions (expires_at)""",
    """CREATE INDEX ix_user_sessions_user_id ON user_sessions (user_id)""",
    """CREATE INDEX ix_user_profiles_institution_id ON user_profiles (institution_id)""",
]


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    for statement in STATEMENTS:
        op.execute(statement)
    op.execute("""
        CREATE FUNCTION dormiostay_alias_text(text[]) RETURNS text
        LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
        AS $$ SELECT array_to_string($1, ' ') $$
    """)
    op.execute(
        "CREATE INDEX ix_locations_aliases_trgm ON locations USING gin "
        "(dormiostay_alias_text(aliases) gin_trgm_ops)"
    )
    op.execute("""
        CREATE FUNCTION dormiostay_audit_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            RAISE EXCEPTION 'audit_logs is append-only' USING ERRCODE = '42501';
        END $$
    """)
    op.execute(
        "CREATE TRIGGER audit_immutable BEFORE UPDATE OR DELETE OR TRUNCATE "
        "ON audit_logs FOR EACH STATEMENT EXECUTE FUNCTION dormiostay_audit_immutable()"
    )
    op.execute("ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE audit_logs FORCE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY audit_insert ON audit_logs FOR INSERT WITH CHECK (true)")
    op.execute(
        "CREATE POLICY audit_admin_read ON audit_logs FOR SELECT "
        "USING (current_setting('app.role', true) = 'admin')"
    )
    op.execute("REVOKE ALL ON audit_logs FROM PUBLIC")


def downgrade() -> None:
    for table in [
        "audit_logs",
        "user_profiles",
        "user_sessions",
        "email_verification_tokens",
        "password_reset_tokens",
        "platform_settings",
        "property_categories",
        "amenities",
        "institutions",
        "users",
        "locations",
    ]:
        op.execute('DROP TABLE "' + table + '"')
    op.execute("DROP FUNCTION dormiostay_audit_immutable()")
    op.execute("DROP FUNCTION dormiostay_alias_text(text[])")
    # pg_trgm may be shared with unrelated objects; preserve the extension.

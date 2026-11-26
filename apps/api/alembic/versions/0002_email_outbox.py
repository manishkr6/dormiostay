"""Add email_outbox table (T-09)."""

from alembic import op

revision = "0002_email_outbox"
down_revision = "0001_foundation"
branch_labels = None
depends_on = None

UPGRADE_STATEMENTS = [
    """CREATE TABLE email_outbox (
    recipient TEXT NOT NULL,
    recipient_name TEXT,
    subject TEXT NOT NULL,
    html_content TEXT NOT NULL,
    text_content TEXT NOT NULL,
    tags TEXT[] DEFAULT '{}' NOT NULL,
    status TEXT DEFAULT 'queued' NOT NULL,
    attempts INTEGER DEFAULT 0 NOT NULL,
    max_attempts INTEGER DEFAULT 5 NOT NULL,
    last_error TEXT,
    send_after TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    sent_at TIMESTAMP WITH TIME ZONE,
    delivered_at TIMESTAMP WITH TIME ZONE,
    failed_at TIMESTAMP WITH TIME ZONE,
    provider_message_id TEXT,
    metadata JSONB,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_email_outbox PRIMARY KEY (id),
    CONSTRAINT ck_email_outbox_status CHECK
        (status IN ('queued', 'sending', 'accepted_by_provider', 'delivered', 'failed')),
    CONSTRAINT ck_email_outbox_attempts CHECK (attempts >= 0),
    CONSTRAINT ck_email_outbox_max_attempts CHECK (max_attempts > 0)
)""",
    """CREATE INDEX ix_email_outbox_status_send_after ON email_outbox (status, send_after)""",
    """CREATE INDEX ix_email_outbox_provider_message_id ON email_outbox (provider_message_id)""",
]

DOWNGRADE_STATEMENTS = [
    """DROP TABLE IF EXISTS email_outbox CASCADE""",
]


def upgrade() -> None:
    for stmt in UPGRADE_STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DOWNGRADE_STATEMENTS:
        op.execute(stmt)

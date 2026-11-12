"""People, agents and the append-only audit trail."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "people",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("email", sa.String(200), nullable=False, unique=True),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("role", sa.String(40), nullable=False),
        sa.Column("department", sa.String(60), nullable=False),
        sa.Column("is_admin", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "agents",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("person_id", sa.String(40), sa.ForeignKey("people.id"), unique=True),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("bundle", sa.String(40), nullable=False),
        sa.Column("tier", sa.String(20), nullable=False, server_default="strict"),
        sa.Column("state", sa.String(20), nullable=False, server_default="provisioning"),
        sa.Column("suspended", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("model_key_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("connector_token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("last_activity_at", _TS),
        sa.Column("last_wake_seconds", sa.Float),
        sa.Column("wake_count", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("outcome", sa.String(20), nullable=False, server_default="info"),
        sa.Column("person_id", sa.String(40)),
        sa.Column("agent_id", sa.String(40)),
        sa.Column("case_id", sa.String(40)),
        sa.Column("system", sa.String(40)),
        sa.Column("run_id", sa.String(60)),
        sa.Column("trace_id", sa.String(64)),
        sa.Column("summary", sa.Text, nullable=False, server_default=""),
        sa.Column("detail", postgresql.JSONB, nullable=False, server_default="{}"),
    )
    for column in ("at", "kind", "person_id", "agent_id", "case_id", "system"):
        op.create_index(f"ix_audit_events_{column}", "audit_events", [column])
    # Append-only: the database itself refuses to change or remove audit records.
    op.execute(
        """
        create function audit_events_append_only() returns trigger language plpgsql as $$
        begin
            raise exception 'audit_events is append-only';
        end $$;
        """
    )
    op.execute(
        """
        create trigger audit_events_no_change
        before update or delete or truncate on audit_events
        for each statement execute function audit_events_append_only();
        """
    )


def downgrade() -> None:
    op.execute("drop trigger audit_events_no_change on audit_events")
    op.execute("drop function audit_events_append_only()")
    op.drop_table("audit_events")
    op.drop_table("agents")
    op.drop_table("people")

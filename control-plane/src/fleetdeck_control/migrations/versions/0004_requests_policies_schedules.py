"""Requests between agents, the policies that govern them, and schedules."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

_TS = sa.DateTime(timezone=True)
_NOW = sa.func.now()


def upgrade() -> None:
    op.create_table(
        "request_policies",
        sa.Column("request_type", sa.String(30), primary_key=True),
        sa.Column("from_role", sa.String(40), primary_key=True),
        sa.Column("to_role", sa.String(40), primary_key=True),
        sa.Column("approval", sa.String(10), nullable=False, server_default="required"),
        sa.Column("grants_membership", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("note", sa.Text, nullable=False, server_default=""),
    )
    op.create_table(
        "requests",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("type", sa.String(30), nullable=False),
        sa.Column("from_agent", sa.String(40), nullable=False, index=True),
        sa.Column("from_person", sa.String(40), index=True),
        sa.Column("to_agent", sa.String(40), nullable=False, index=True),
        sa.Column("to_person", sa.String(40), index=True),
        sa.Column("case_id", sa.String(40), nullable=False, index=True),
        sa.Column("data_class", sa.String(20), nullable=False, server_default="confidential"),
        sa.Column("subject", sa.String(200), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, index=True),
        sa.Column("reason", sa.Text, nullable=False, server_default=""),
        sa.Column("reply_body", sa.Text),
        sa.Column("approval", sa.String(10), nullable=False, server_default="required"),
        sa.Column("from_session_id", sa.String(40)),
        sa.Column("handler_session_id", sa.String(40)),
        sa.Column("created_at", _TS, nullable=False, server_default=_NOW),
        sa.Column("answered_at", _TS),
    )
    op.create_table(
        "schedules",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column(
            "agent_id",
            sa.String(40),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("cron", sa.String(60), nullable=False),
        sa.Column("prompt", sa.Text, nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("next_run_at", _TS, index=True),
        sa.Column("last_run_at", _TS),
        sa.Column("last_run_id", sa.String(40)),
    )


def downgrade() -> None:
    op.drop_table("schedules")
    op.drop_table("requests")
    op.drop_table("request_policies")

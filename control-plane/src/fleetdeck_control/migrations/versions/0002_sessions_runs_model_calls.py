"""Sessions, runs, transcripts and model calls."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

_TS = sa.DateTime(timezone=True)
_NOW = sa.func.now()


def upgrade() -> None:
    op.add_column("agents", sa.Column("current_run_id", sa.String(40)))
    op.create_table(
        "agent_sessions",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column(
            "agent_id",
            sa.String(40),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("title", sa.String(200), nullable=False, server_default=""),
        sa.Column("case_id", sa.String(40)),
        sa.Column("data_class", sa.String(20), nullable=False, server_default="general"),
        sa.Column("created_at", _TS, nullable=False, server_default=_NOW),
    )
    op.create_table(
        "runs",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column(
            "agent_id",
            sa.String(40),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "session_id",
            sa.String(40),
            sa.ForeignKey("agent_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("trigger", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("input", sa.Text, nullable=False),
        sa.Column("output", sa.Text),
        sa.Column("error", sa.Text),
        sa.Column("hermes_run_id", sa.String(60)),
        sa.Column("trace_id", sa.String(64)),
        sa.Column("wake_seconds", sa.Float),
        sa.Column("input_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", _TS, nullable=False, server_default=_NOW),
        sa.Column("started_at", _TS),
        sa.Column("ended_at", _TS),
    )
    op.create_table(
        "messages",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(40),
            sa.ForeignKey("agent_sessions.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("run_id", sa.String(40)),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("meta", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", _TS, nullable=False, server_default=_NOW),
    )
    op.create_table(
        "model_calls",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("at", _TS, nullable=False, server_default=_NOW, index=True),
        sa.Column("agent_id", sa.String(40), nullable=False, index=True),
        sa.Column("person_id", sa.String(40), index=True),
        sa.Column("department", sa.String(60)),
        sa.Column("run_id", sa.String(40), index=True),
        sa.Column("session_id", sa.String(40)),
        sa.Column("case_id", sa.String(40)),
        sa.Column("trigger", sa.String(20)),
        sa.Column("data_class", sa.String(20), nullable=False),
        sa.Column("route", sa.String(20), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("input_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cached_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Float, nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer, nullable=False, server_default="0"),
        sa.Column("status", sa.Integer, nullable=False, server_default="200"),
        sa.Column("trace_id", sa.String(64)),
    )


def downgrade() -> None:
    op.drop_table("model_calls")
    op.drop_table("messages")
    op.drop_table("runs")
    op.drop_table("agent_sessions")
    op.drop_column("agents", "current_run_id")

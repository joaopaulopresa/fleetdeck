"""Runtime settings and skill proposals."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "settings",
        sa.Column("key", sa.String(60), primary_key=True),
        sa.Column("value", postgresql.JSONB, nullable=False),
    )
    op.create_table(
        "skills",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("description", sa.String(300), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("scope", sa.String(10), nullable=False),
        sa.Column("owner_person_id", sa.String(40), index=True),
        sa.Column("proposed_by_agent", sa.String(40), nullable=False),
        sa.Column("roles", postgresql.ARRAY(sa.String(40)), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(10), nullable=False, server_default="proposed", index=True),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("decided_by", sa.String(40)),
        sa.Column("decided_at", _TS),
    )


def downgrade() -> None:
    op.drop_table("skills")
    op.drop_table("settings")

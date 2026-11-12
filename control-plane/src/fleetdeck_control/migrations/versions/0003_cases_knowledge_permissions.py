"""Case spaces and the knowledge base under row-level security; tool permissions; approvals."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

_TS = sa.DateTime(timezone=True)
_NOW = sa.func.now()
_PERSON = "current_setting('app.person_id', true)"


def upgrade() -> None:
    op.create_table(
        "cases",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("summary", sa.Text, nullable=False, server_default=""),
        sa.Column("data_class", sa.String(20), nullable=False, server_default="confidential"),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("created_at", _TS, nullable=False, server_default=_NOW),
    )
    op.create_table(
        "case_members",
        sa.Column(
            "case_id",
            sa.String(40),
            sa.ForeignKey("cases.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "person_id",
            sa.String(40),
            sa.ForeignKey("people.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("role_in_case", sa.String(20), nullable=False, server_default="member"),
        sa.Column("added_at", _TS, nullable=False, server_default=_NOW),
    )
    op.create_table(
        "kb_documents",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("case_id", sa.String(40), sa.ForeignKey("cases.id", ondelete="CASCADE")),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("data_class", sa.String(20), nullable=False, server_default="general"),
        sa.Column(
            "audience_roles", postgresql.ARRAY(sa.String(40)), nullable=False, server_default="{}"
        ),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="published"),
        sa.Column("author_person_id", sa.String(40)),
        sa.Column("created_at", _TS, nullable=False, server_default=_NOW),
        sa.Column(
            "search",
            postgresql.TSVECTOR,
            sa.Computed("to_tsvector('english', title || ' ' || body)", persisted=True),
        ),
    )
    op.create_index("ix_kb_documents_search", "kb_documents", ["search"], postgresql_using="gin")
    op.create_index("ix_kb_documents_case_id", "kb_documents", ["case_id"])
    op.create_table(
        "systems",
        sa.Column("name", sa.String(40), primary_key=True),
        sa.Column("title", sa.String(80), nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("data_class", sa.String(20), nullable=False, server_default="confidential"),
    )
    op.create_table(
        "tool_permissions",
        sa.Column(
            "system",
            sa.String(40),
            sa.ForeignKey("systems.name", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("tool", sa.String(60), primary_key=True),
        sa.Column("role", sa.String(40), primary_key=True),
        sa.Column("scope", sa.String(10), nullable=False, server_default="all"),
    )
    op.create_table(
        "approvals",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column(
            "person_id", sa.String(40), sa.ForeignKey("people.id"), nullable=False, index=True
        ),
        sa.Column("agent_id", sa.String(40)),
        sa.Column("case_id", sa.String(40)),
        sa.Column("system", sa.String(40), nullable=False),
        sa.Column("ref", sa.String(60), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.Text, nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending", index=True),
        sa.Column("created_at", _TS, nullable=False, server_default=_NOW),
        sa.Column("decided_at", _TS),
        sa.Column("detail", postgresql.JSONB, nullable=False, server_default="{}"),
    )

    # --- Row-level security -------------------------------------------------------------
    # fleet_scoped is the role every person-scoped query runs as: the knowledge connector logs
    # in with a member of it, and the API switches to it for the employee's own requests. It
    # owns nothing, so the policies below always apply to it.
    op.execute(
        """
        do $$ begin
            if not exists (select from pg_roles where rolname = 'fleet_scoped') then
                create role fleet_scoped nologin;
            end if;
        end $$;
        """
    )
    op.execute("grant usage on schema public to fleet_scoped")
    op.execute("grant select on people, cases, case_members, kb_documents to fleet_scoped")
    op.execute("grant insert on kb_documents to fleet_scoped")
    # Membership and role lookups run with the owner's rights so that the policies can use
    # them without recursing into the tables they protect.
    op.execute(
        f"""
        create function is_case_member(target text) returns boolean
        language sql stable security definer set search_path = public as $$
            select exists (
                select 1 from case_members
                where case_id = target and person_id = {_PERSON}
            )
        $$;
        """
    )
    op.execute(
        f"""
        create function current_person_role() returns text
        language sql stable security definer set search_path = public as $$
            select role from people where id = {_PERSON}
        $$;
        """
    )
    for table in ("cases", "case_members", "kb_documents"):
        op.execute(f"alter table {table} enable row level security")
    op.execute(
        "create policy members_see_their_cases on cases for select to fleet_scoped "
        "using (is_case_member(id))"
    )
    op.execute(
        "create policy members_see_the_member_list on case_members for select to fleet_scoped "
        "using (is_case_member(case_id))"
    )
    op.execute(
        """
        create policy readers on kb_documents for select to fleet_scoped using (
            (case_id is not null and is_case_member(case_id))
            or (case_id is null and status = 'published'
                and (cardinality(audience_roles) = 0
                     or current_person_role() = any (audience_roles)))
        )
        """
    )
    op.execute(
        f"""
        create policy members_add_drafts on kb_documents for insert to fleet_scoped with check (
            case_id is not null and is_case_member(case_id)
            and status = 'draft' and author_person_id = {_PERSON}
        )
        """
    )


def downgrade() -> None:
    for table in (
        "approvals",
        "tool_permissions",
        "systems",
        "kb_documents",
        "case_members",
        "cases",
    ):
        op.drop_table(table)
    op.execute("drop function is_case_member(text)")
    op.execute("drop function current_person_role()")

"""audit_events: who did what, append-only

Revision ID: a4d9e2b7c1f5
Revises: e8c2f5a1d9b7

Admin actions reach Keycloak as gen9-agent's service account, so only Gen9 knows which admin
acted (docs/plans/harness.md, "Auth across the harness"; OWASP ASVS 5.0 V16). A trigger makes the
table append-only: rows are added and read, never changed or deleted.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a4d9e2b7c1f5"
down_revision: str | Sequence[str] | None = "e8c2f5a1d9b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "actor",
            sa.String(length=255),
            nullable=False,
            comment="The person's sub, or who else acted (gen9-agent)",
        ),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column(
            "outcome", sa.String(length=16), nullable=False, comment="success or denied"
        ),
        sa.Column("target", sa.String(length=255), nullable=True),
        sa.Column(
            "where",
            sa.String(length=255),
            nullable=True,
            comment="The route, as its template: PATCH /v1/admin/users/{user_id}",
        ),
        sa.Column(
            "detail",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_events_at", "audit_events", ["at"])
    op.create_index("ix_audit_events_actor", "audit_events", ["actor"])
    op.create_index("ix_audit_events_action", "audit_events", ["action"])
    op.execute(
        """
        create function audit_events_append_only() returns trigger language plpgsql as $$
        begin
          raise exception 'audit_events is append-only';
        end $$
        """
    )
    op.execute(
        """
        create trigger audit_events_append_only
        before update or delete on audit_events
        for each row execute function audit_events_append_only()
        """
    )
    # Row triggers don't see TRUNCATE
    op.execute(
        """
        create trigger audit_events_no_truncate
        before truncate on audit_events
        for each statement execute function audit_events_append_only()
        """
    )


def downgrade() -> None:
    op.execute("drop trigger audit_events_no_truncate on audit_events")
    op.execute("drop trigger audit_events_append_only on audit_events")
    op.execute("drop function audit_events_append_only()")
    op.drop_table("audit_events")

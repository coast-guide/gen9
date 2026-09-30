"""Memory left by deleted accounts: the file saying memory is off is keyed by the person's sub,
and account deletion didn't erase it until memory.erase_memory took both of their namespaces
(docs/plans/manual-e2e.md, I14). This deletes what earlier deletions left: memory rows, in
either namespace, whose person Gen9 no longer has.

On a fresh database LangGraph's store doesn't exist yet (migrate.py sets it up after these
migrations), so there is nothing to do. The store keeps a namespace as its parts joined by dots
(`memories-off.<sub>`), in agent.py's CHECKPOINT_SCHEMA.

Revision ID: b7e2c4f9a1d6
Revises: c3e7a1f9d2b8
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b7e2c4f9a1d6"
down_revision: str | None = "c3e7a1f9d2b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF to_regclass('langgraph.store') IS NOT NULL THEN
            DELETE FROM langgraph.store s
            WHERE (s.prefix LIKE 'memories-off.%' OR s.prefix LIKE 'memories.%')
              AND NOT EXISTS (
                SELECT 1 FROM users u
                WHERE s.prefix IN ('memories-off.' || u.sub, 'memories.' || u.sub)
              );
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    """What went isn't coming back, and needn't: it belonged to people who are gone."""

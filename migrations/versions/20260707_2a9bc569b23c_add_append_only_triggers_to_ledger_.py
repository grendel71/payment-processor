"""add append-only triggers to ledger_entries and audit_events

Revision ID: 2a9bc569b23c
Revises: 6ed9acea6544
Create Date: 2026-07-07 01:37:32.218254

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2a9bc569b23c'
down_revision: Union[str, None] = '6ed9acea6544'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION pp_reject_mutation()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only; UPDATE/DELETE forbidden', TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tr_ledger_entries_append_only
            BEFORE UPDATE OR DELETE ON ledger_entries
            FOR EACH ROW EXECUTE FUNCTION pp_reject_mutation();
    """)
    op.execute("""
        CREATE TRIGGER tr_audit_events_append_only
            BEFORE UPDATE OR DELETE ON audit_events
            FOR EACH ROW EXECUTE FUNCTION pp_reject_mutation();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS tr_audit_events_append_only ON audit_events;")
    op.execute("DROP TRIGGER IF EXISTS tr_ledger_entries_append_only ON ledger_entries;")
    op.execute("DROP FUNCTION IF EXISTS pp_reject_mutation();")

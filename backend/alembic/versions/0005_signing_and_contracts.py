"""Код подписания, причина версии договора и сквозные номера договоров

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("applications", sa.Column("signing_code", sa.String(16), nullable=True))
    op.add_column(
        "applications",
        sa.Column("signing_code_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("contract_versions", sa.Column("reason", sa.Text(), nullable=True))
    # До этапа 8 договоров не создавалось, поэтому changes в старом формате (словарь) в базе нет.
    op.execute(sa.schema.CreateSequence(sa.Sequence("contract_number_seq")))


def downgrade() -> None:
    op.execute(sa.schema.DropSequence(sa.Sequence("contract_number_seq")))
    op.drop_column("contract_versions", "reason")
    op.drop_column("applications", "signing_code_expires_at")
    op.drop_column("applications", "signing_code")

"""Прогон конвейера у заявки и поломки моков в БД

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("applications", sa.Column("pipeline_run_id", sa.Uuid(), nullable=True))
    # Заявки, которые уже ждут конвейер, получают run_id, иначе reconciler поставил бы задачу без него.
    op.execute(
        "UPDATE applications SET pipeline_run_id = gen_random_uuid() "
        "WHERE status IN ('SUBMITTED', 'CHECKING', 'SCORING')"
    )
    op.create_table(
        "mock_failures",
        sa.Column("source", sa.String(32), primary_key=True),
        sa.Column("inn", sa.String(12), primary_key=True),
    )


def downgrade() -> None:
    op.drop_table("mock_failures")
    op.drop_column("applications", "pipeline_run_id")

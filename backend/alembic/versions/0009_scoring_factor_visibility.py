"""Пояснение и видимость фактора скоринга: антифрод-факторы клиенту не показываются

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-26
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Коды антифрод-факторов ScoringEngine (client_visible=False) на момент миграции.
ANTIFRAUD_CODES = ("MASS_ADDRESS", "MASS_DIRECTOR", "CREDIT_INQUIRIES")


def upgrade() -> None:
    op.add_column(
        "scoring_factors",
        sa.Column("explanation", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "scoring_factors",
        sa.Column("client_visible", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    # Уже посчитанные заявки иначе показали бы клиенту антифрод-факторы.
    factors = sa.table("scoring_factors", sa.column("code"), sa.column("client_visible"))
    op.execute(
        factors.update()
        .where(factors.c.code.in_(ANTIFRAUD_CODES))
        .values(client_visible=False)
    )
    op.alter_column("scoring_factors", "explanation", server_default=None)
    op.alter_column("scoring_factors", "client_visible", server_default=None)


def downgrade() -> None:
    op.drop_column("scoring_factors", "client_visible")
    op.drop_column("scoring_factors", "explanation")

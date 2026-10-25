"""Название компании и ОКВЭД в заявке: карточка заявки не ходит в ЕГРЮЛ

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # server_default только для уже существующих строк, новые заявки всегда передают значения.
    op.add_column(
        "applications",
        sa.Column("company_name", sa.String(length=255), nullable=False, server_default=""),
    )
    op.add_column(
        "applications", sa.Column("okved", sa.String(length=16), nullable=False, server_default="")
    )
    op.alter_column("applications", "company_name", server_default=None)
    op.alter_column("applications", "okved", server_default=None)


def downgrade() -> None:
    op.drop_column("applications", "okved")
    op.drop_column("applications", "company_name")

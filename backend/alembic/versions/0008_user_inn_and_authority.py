"""ИНН физлица у пользователя и основание полномочий заявителя

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Без бэкфилла: ИНН вводит сам человек, а демо-базу пересоздают вместе с сидом.
    op.add_column("users", sa.Column("inn", sa.String(12), nullable=True))
    op.create_unique_constraint("uq_users_inn", "users", ["inn"])
    op.add_column("user_companies", sa.Column("authority", postgresql.JSONB(), nullable=True))
    op.add_column("applications", sa.Column("authority", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("applications", "authority")
    op.drop_column("user_companies", "authority")
    op.drop_constraint("uq_users_inn", "users", type_="unique")
    op.drop_column("users", "inn")

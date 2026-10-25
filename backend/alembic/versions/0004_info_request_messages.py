"""Пояснения аналитика к пунктам дозапроса

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # server_default только для уже существующих дозапросов, новые всегда передают значение.
    op.add_column(
        "info_requests",
        sa.Column(
            "item_messages",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.alter_column("info_requests", "item_messages", server_default=None)


def downgrade() -> None:
    op.drop_column("info_requests", "item_messages")

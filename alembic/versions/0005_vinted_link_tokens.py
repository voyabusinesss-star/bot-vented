"""vinted link tokens for self-service member linking

Revision ID: 0005_vinted_link_tokens
Revises: 0004_autobuy_validated
Create Date: 2026-07-25

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_vinted_link_tokens"
down_revision: Union[str, Sequence[str], None] = "0004_autobuy_validated"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "vinted_link_tokens",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("discord_user_id", sa.BigInteger(), nullable=False),
        sa.Column("discord_username", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token", name="uq_vinted_link_token"),
    )
    op.create_index(
        "ix_vinted_link_tokens_discord_user_id",
        "vinted_link_tokens",
        ["discord_user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_vinted_link_tokens_discord_user_id", table_name="vinted_link_tokens")
    op.drop_table("vinted_link_tokens")

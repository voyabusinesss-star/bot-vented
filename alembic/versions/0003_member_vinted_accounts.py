"""member vinted accounts for autobuy

Revision ID: 0003_member_vinted
Revises: 0002_discord_posted
Create Date: 2026-07-25

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_member_vinted"
down_revision: Union[str, Sequence[str], None] = "0002_discord_posted"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "member_vinted_accounts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("discord_user_id", sa.BigInteger(), nullable=False),
        sa.Column("discord_username", sa.String(length=255), nullable=True),
        sa.Column("vinted_username", sa.String(length=255), nullable=True),
        sa.Column(
            "storage_state",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "linked_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("discord_user_id", name="uq_member_vinted_discord_user"),
    )
    op.create_index(
        "ix_member_vinted_accounts_discord_user_id",
        "member_vinted_accounts",
        ["discord_user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_member_vinted_accounts_discord_user_id",
        table_name="member_vinted_accounts",
    )
    op.drop_table("member_vinted_accounts")

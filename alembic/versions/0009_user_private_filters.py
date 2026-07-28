"""Private user filters + Discord member plans

Revision ID: 0009_user_private_filters
Revises: 0008_opportunity_history
Create Date: 2026-07-27

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009_user_private_filters"
down_revision: Union[str, Sequence[str], None] = "0008_opportunity_history"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "discord_member_plans",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("discord_user_id", sa.BigInteger(), nullable=False),
        sa.Column("discord_username", sa.String(length=255), nullable=True),
        sa.Column(
            "plan",
            sa.String(length=32),
            server_default="starter",
            nullable=False,
        ),
        sa.Column(
            "created_at",
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("discord_user_id", name="uq_discord_member_plan_user"),
    )
    op.create_index(
        "ix_discord_member_plans_discord_user_id",
        "discord_member_plans",
        ["discord_user_id"],
        unique=False,
    )

    op.create_table(
        "user_filters",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("discord_user_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=True),
        sa.Column("brand", sa.String(length=120), nullable=True),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("category", sa.String(length=120), nullable=True),
        sa.Column("keyword", sa.String(length=255), nullable=True),
        sa.Column("max_price_eur", sa.Float(), nullable=True),
        sa.Column("min_price_eur", sa.Float(), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default="true",
            nullable=False,
        ),
        sa.Column(
            "created_at",
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
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_user_filters_discord_user_id",
        "user_filters",
        ["discord_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_user_filters_active",
        "user_filters",
        ["is_active"],
        unique=False,
    )

    op.create_table(
        "user_filter_alerts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("filter_id", sa.Integer(), nullable=False),
        sa.Column("discord_user_id", sa.BigInteger(), nullable=False),
        sa.Column("vinted_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "sent_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["filter_id"], ["user_filters.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "filter_id",
            "vinted_id",
            name="uq_user_filter_alert_once",
        ),
    )
    op.create_index(
        "ix_user_filter_alerts_user",
        "user_filter_alerts",
        ["discord_user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_user_filter_alerts_user", table_name="user_filter_alerts")
    op.drop_table("user_filter_alerts")
    op.drop_index("ix_user_filters_active", table_name="user_filters")
    op.drop_index("ix_user_filters_discord_user_id", table_name="user_filters")
    op.drop_table("user_filters")
    op.drop_index(
        "ix_discord_member_plans_discord_user_id", table_name="discord_member_plans"
    )
    op.drop_table("discord_member_plans")

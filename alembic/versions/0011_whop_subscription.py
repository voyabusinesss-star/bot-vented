"""Whop subscription_active + membership id on member plans

Revision ID: 0011_whop_subscription
Revises: 0010_resello_dm_dashboard
Create Date: 2026-08-03

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011_whop_subscription"
down_revision: Union[str, Sequence[str], None] = "0010_resello_dm_dashboard"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "discord_member_plans",
        sa.Column(
            "subscription_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "discord_member_plans",
        sa.Column("whop_membership_id", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("discord_member_plans", "whop_membership_id")
    op.drop_column("discord_member_plans", "subscription_active")

"""Resello DM dashboard message ids on member plans

Revision ID: 0010_resello_dm_dashboard
Revises: 0009_user_private_filters
Create Date: 2026-07-27

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010_resello_dm_dashboard"
down_revision: Union[str, Sequence[str], None] = "0009_user_private_filters"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "discord_member_plans",
        sa.Column("dm_channel_id", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "discord_member_plans",
        sa.Column("dm_dashboard_message_id", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("discord_member_plans", "dm_dashboard_message_id")
    op.drop_column("discord_member_plans", "dm_channel_id")

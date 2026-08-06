"""Discord outbox queue for chronological salon posts

Revision ID: 0012_discord_outbox
Revises: 0011_whop_subscription
Create Date: 2026-08-06

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012_discord_outbox"
down_revision: Union[str, Sequence[str], None] = "0011_whop_subscription"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "discord_outbox",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("listing_id", sa.Integer(), nullable=False),
        sa.Column("channel_id", sa.String(length=32), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "enqueued_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.ForeignKeyConstraint(["listing_id"], ["listings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_discord_outbox_listing_id", "discord_outbox", ["listing_id"])
    op.create_index("ix_discord_outbox_channel_id", "discord_outbox", ["channel_id"])
    op.create_index("ix_discord_outbox_published_at", "discord_outbox", ["published_at"])
    op.create_index("ix_discord_outbox_enqueued_at", "discord_outbox", ["enqueued_at"])
    op.create_index("ix_discord_outbox_status", "discord_outbox", ["status"])
    op.create_index(
        "uq_discord_outbox_pending",
        "discord_outbox",
        ["listing_id", "channel_id", "kind"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("uq_discord_outbox_pending", table_name="discord_outbox")
    op.drop_index("ix_discord_outbox_status", table_name="discord_outbox")
    op.drop_index("ix_discord_outbox_enqueued_at", table_name="discord_outbox")
    op.drop_index("ix_discord_outbox_published_at", table_name="discord_outbox")
    op.drop_index("ix_discord_outbox_channel_id", table_name="discord_outbox")
    op.drop_index("ix_discord_outbox_listing_id", table_name="discord_outbox")
    op.drop_table("discord_outbox")

"""Private alert outbox spill table for filter DMs

Revision ID: 0013_private_alert_outbox
Revises: 0012_discord_outbox
Create Date: 2026-08-06

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013_private_alert_outbox"
down_revision: Union[str, Sequence[str], None] = "0012_discord_outbox"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "private_alert_outbox",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("filter_id", sa.Integer(), nullable=False),
        sa.Column("discord_user_id", sa.BigInteger(), nullable=False),
        sa.Column("vinted_id", sa.BigInteger(), nullable=False),
        sa.Column("listing_id", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("title", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("url", sa.Text(), nullable=False, server_default=""),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column(
            "enqueued_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "filter_id",
            "vinted_id",
            name="uq_private_alert_outbox_filter_vinted",
        ),
    )
    op.create_index(
        "ix_private_alert_outbox_filter_id", "private_alert_outbox", ["filter_id"]
    )
    op.create_index(
        "ix_private_alert_outbox_discord_user_id",
        "private_alert_outbox",
        ["discord_user_id"],
    )
    op.create_index(
        "ix_private_alert_outbox_vinted_id", "private_alert_outbox", ["vinted_id"]
    )
    op.create_index(
        "ix_private_alert_outbox_status", "private_alert_outbox", ["status"]
    )
    op.create_index(
        "ix_private_alert_outbox_enqueued_at",
        "private_alert_outbox",
        ["enqueued_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_private_alert_outbox_enqueued_at", table_name="private_alert_outbox")
    op.drop_index("ix_private_alert_outbox_status", table_name="private_alert_outbox")
    op.drop_index("ix_private_alert_outbox_vinted_id", table_name="private_alert_outbox")
    op.drop_index(
        "ix_private_alert_outbox_discord_user_id", table_name="private_alert_outbox"
    )
    op.drop_index("ix_private_alert_outbox_filter_id", table_name="private_alert_outbox")
    op.drop_table("private_alert_outbox")

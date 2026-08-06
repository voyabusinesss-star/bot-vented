"""Pending Whop claims when Discord is not linked at checkout

Revision ID: 0014_whop_pending_claims
Revises: 0013_private_alert_outbox
Create Date: 2026-08-06

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014_whop_pending_claims"
down_revision: Union[str, Sequence[str], None] = "0013_private_alert_outbox"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "whop_pending_claims",
        sa.Column("membership_id", sa.String(length=64), nullable=False),
        sa.Column("plan", sa.String(length=32), nullable=False),
        sa.Column("product_id", sa.String(length=64), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("license_key", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_discord_user_id", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("membership_id"),
    )
    op.create_index(
        "ix_whop_pending_claims_email",
        "whop_pending_claims",
        ["email"],
    )
    op.create_index(
        "ix_whop_pending_claims_claimed_at",
        "whop_pending_claims",
        ["claimed_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_whop_pending_claims_claimed_at", table_name="whop_pending_claims")
    op.drop_index("ix_whop_pending_claims_email", table_name="whop_pending_claims")
    op.drop_table("whop_pending_claims")

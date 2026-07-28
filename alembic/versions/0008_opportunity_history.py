"""Opportunity history for learning loop

Revision ID: 0008_opportunity_history
Revises: 0007_trend_snapshots
Create Date: 2026-07-26

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_opportunity_history"
down_revision: Union[str, Sequence[str], None] = "0007_trend_snapshots"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "opportunity_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("niche_key", sa.String(length=512), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("lifecycle", sa.String(length=32), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("niche_type", sa.String(length=32), nullable=True),
        sa.Column("brand_slug", sa.String(length=128), nullable=True),
        sa.Column("model_slug", sa.String(length=128), nullable=True),
        sa.Column("category_slug", sa.String(length=64), nullable=True),
        sa.Column("signals", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("posted", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_opportunity_history_niche_key",
        "opportunity_history",
        ["niche_key"],
        unique=False,
    )
    op.create_index(
        "ix_opportunity_history_detected_at",
        "opportunity_history",
        ["detected_at"],
        unique=False,
    )
    op.create_index(
        "ix_opportunity_history_score",
        "opportunity_history",
        ["score"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_opportunity_history_score", table_name="opportunity_history")
    op.drop_index("ix_opportunity_history_detected_at", table_name="opportunity_history")
    op.drop_index("ix_opportunity_history_niche_key", table_name="opportunity_history")
    op.drop_table("opportunity_history")

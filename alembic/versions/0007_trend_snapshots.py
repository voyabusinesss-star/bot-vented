"""Trend snapshots for continuous market radar + daily report

Revision ID: 0007_trend_snapshots
Revises: 0006_market_intel
Create Date: 2026-07-26

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_trend_snapshots"
down_revision: Union[str, Sequence[str], None] = "0006_market_intel"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "trend_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("entity_key", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("strength", sa.Float(), nullable=False),
        sa.Column("direction", sa.String(length=32), nullable=True),
        sa.Column("lifecycle", sa.String(length=32), nullable=True),
        sa.Column("importance", sa.String(length=32), nullable=True),
        sa.Column("recommendation", sa.String(length=32), nullable=True),
        sa.Column("count_1d", sa.Integer(), server_default="0", nullable=False),
        sa.Column("count_7d", sa.Integer(), server_default="0", nullable=False),
        sa.Column("count_30d", sa.Integer(), server_default="0", nullable=False),
        sa.Column("count_90d", sa.Integer(), server_default="0", nullable=False),
        sa.Column("active_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("disappeared_7d", sa.Integer(), server_default="0", nullable=False),
        sa.Column("price_median_7d", sa.Float(), nullable=True),
        sa.Column("price_median_30d", sa.Float(), nullable=True),
        sa.Column("price_change_pct", sa.Float(), nullable=True),
        sa.Column("rotation_change_pct", sa.Float(), nullable=True),
        sa.Column("stock_change_pct", sa.Float(), nullable=True),
        sa.Column("popularity_change_pct", sa.Float(), nullable=True),
        sa.Column("continuation_pct", sa.Float(), nullable=True),
        sa.Column("gauge_growth", sa.Float(), nullable=True),
        sa.Column("gauge_rentabilite", sa.Float(), nullable=True),
        sa.Column("gauge_rarity", sa.Float(), nullable=True),
        sa.Column("gauge_demand", sa.Float(), nullable=True),
        sa.Column("gauge_saturation", sa.Float(), nullable=True),
        sa.Column(
            "triggers",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "snapshot_date",
            "entity_type",
            "entity_key",
            name="uq_trend_snapshot_day_entity",
        ),
    )
    op.create_index(
        "ix_trend_snapshots_snapshot_date", "trend_snapshots", ["snapshot_date"]
    )
    op.create_index(
        "ix_trend_snapshots_strength", "trend_snapshots", ["strength"]
    )
    op.create_index(
        "ix_trend_snapshots_entity",
        "trend_snapshots",
        ["entity_type", "entity_key"],
    )
    op.create_index(
        "ix_trend_snapshots_importance", "trend_snapshots", ["importance"]
    )


def downgrade() -> None:
    op.drop_index("ix_trend_snapshots_importance", table_name="trend_snapshots")
    op.drop_index("ix_trend_snapshots_entity", table_name="trend_snapshots")
    op.drop_index("ix_trend_snapshots_strength", table_name="trend_snapshots")
    op.drop_index("ix_trend_snapshots_snapshot_date", table_name="trend_snapshots")
    op.drop_table("trend_snapshots")

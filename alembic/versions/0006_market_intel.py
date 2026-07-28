"""Market intelligence: observations, entities, niche snapshots

Revision ID: 0006_market_intel
Revises: 0005_vinted_link_tokens
Create Date: 2026-07-26

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_market_intel"
down_revision: Union[str, Sequence[str], None] = "0005_vinted_link_tokens"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "listings",
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "listings",
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("listings", sa.Column("seller_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "listings", sa.Column("category_slug", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "listings", sa.Column("model_slug", sa.String(length=128), nullable=True)
    )
    op.add_column(
        "listings",
        sa.Column("keyword_slugs", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_index("ix_listings_seller_id", "listings", ["seller_id"])
    op.create_index("ix_listings_category_slug", "listings", ["category_slug"])
    op.create_index("ix_listings_model_slug", "listings", ["model_slug"])
    op.create_index("ix_listings_last_seen_at", "listings", ["last_seen_at"])
    op.create_index("ix_listings_is_active", "listings", ["is_active"])

    op.create_table(
        "listing_observations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("listing_id", sa.Integer(), nullable=False),
        sa.Column("vinted_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("price_cents", sa.Integer(), nullable=True),
        sa.Column("is_present", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=True),
        sa.Column("brand", sa.String(length=255), nullable=True),
        sa.Column("size", sa.String(length=64), nullable=True),
        sa.Column("source_query", sa.String(length=512), nullable=True),
        sa.Column("raw_hash", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["listing_id"], ["listings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_listing_observations_listing_id", "listing_observations", ["listing_id"]
    )
    op.create_index(
        "ix_listing_observations_vinted_id", "listing_observations", ["vinted_id"]
    )
    op.create_index(
        "ix_listing_observations_observed_at", "listing_observations", ["observed_at"]
    )
    op.create_index(
        "ix_listing_observations_source_query",
        "listing_observations",
        ["source_query"],
    )

    op.create_table(
        "market_brands",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("slug", sa.String(length=128), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_market_brands_slug"),
    )

    op.create_table(
        "market_models",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("brand_id", sa.Integer(), nullable=True),
        sa.Column("slug", sa.String(length=128), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column(
            "aliases", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["brand_id"], ["market_brands.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_market_models_slug"),
    )
    op.create_index("ix_market_models_brand_id", "market_models", ["brand_id"])

    op.create_table(
        "market_keywords",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("slug", sa.String(length=128), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False, server_default="style"),
        sa.Column(
            "aliases", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_market_keywords_slug"),
    )

    op.create_table(
        "listing_entities",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("listing_id", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("entity_slug", sa.String(length=128), nullable=False),
        sa.Column("confidence", sa.Integer(), server_default="100", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["listing_id"], ["listings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "listing_id",
            "entity_type",
            "entity_slug",
            name="uq_listing_entity",
        ),
    )
    op.create_index("ix_listing_entities_listing_id", "listing_entities", ["listing_id"])
    op.create_index(
        "ix_listing_entities_entity",
        "listing_entities",
        ["entity_type", "entity_slug"],
    )

    op.create_table(
        "niche_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("niche_key", sa.String(length=512), nullable=False),
        sa.Column("window", sa.String(length=8), nullable=False),
        sa.Column("brand_slug", sa.String(length=128), nullable=True),
        sa.Column("model_slug", sa.String(length=128), nullable=True),
        sa.Column("category_slug", sa.String(length=64), nullable=True),
        sa.Column("keyword_flags", sa.String(length=255), nullable=True),
        sa.Column("listing_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("new_listings", sa.Integer(), server_default="0", nullable=False),
        sa.Column("disappeared_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("unique_sellers", sa.Integer(), server_default="0", nullable=False),
        sa.Column("price_min_cents", sa.Integer(), nullable=True),
        sa.Column("price_max_cents", sa.Integer(), nullable=True),
        sa.Column("price_mean_cents", sa.Integer(), nullable=True),
        sa.Column("price_median_cents", sa.Integer(), nullable=True),
        sa.Column("price_p25_cents", sa.Integer(), nullable=True),
        sa.Column("median_ttl_days", sa.Float(), nullable=True),
        sa.Column("margin_proxy_pct", sa.Float(), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("niche_key", "window", name="uq_niche_snapshot_key_window"),
    )
    op.create_index("ix_niche_snapshots_score", "niche_snapshots", ["score"])
    op.create_index("ix_niche_snapshots_window", "niche_snapshots", ["window"])
    op.create_index("ix_niche_snapshots_brand", "niche_snapshots", ["brand_slug"])


def downgrade() -> None:
    op.drop_index("ix_niche_snapshots_brand", table_name="niche_snapshots")
    op.drop_index("ix_niche_snapshots_window", table_name="niche_snapshots")
    op.drop_index("ix_niche_snapshots_score", table_name="niche_snapshots")
    op.drop_table("niche_snapshots")

    op.drop_index("ix_listing_entities_entity", table_name="listing_entities")
    op.drop_index("ix_listing_entities_listing_id", table_name="listing_entities")
    op.drop_table("listing_entities")

    op.drop_table("market_keywords")
    op.drop_index("ix_market_models_brand_id", table_name="market_models")
    op.drop_table("market_models")
    op.drop_table("market_brands")

    op.drop_index("ix_listing_observations_source_query", table_name="listing_observations")
    op.drop_index("ix_listing_observations_observed_at", table_name="listing_observations")
    op.drop_index("ix_listing_observations_vinted_id", table_name="listing_observations")
    op.drop_index("ix_listing_observations_listing_id", table_name="listing_observations")
    op.drop_table("listing_observations")

    op.drop_index("ix_listings_is_active", table_name="listings")
    op.drop_index("ix_listings_last_seen_at", table_name="listings")
    op.drop_index("ix_listings_model_slug", table_name="listings")
    op.drop_index("ix_listings_category_slug", table_name="listings")
    op.drop_index("ix_listings_seller_id", table_name="listings")
    op.drop_column("listings", "keyword_slugs")
    op.drop_column("listings", "model_slug")
    op.drop_column("listings", "category_slug")
    op.drop_column("listings", "seller_id")
    op.drop_column("listings", "last_seen_at")
    op.drop_column("listings", "first_seen_at")

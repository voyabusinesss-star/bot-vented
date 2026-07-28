"""Modèles SQLAlchemy."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Listing(Base):
    __tablename__ = "listings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vinted_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)

    title: Mapped[str] = mapped_column(String(512))
    price_cents: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="EUR")

    brand: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    size: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    condition: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    url: Mapped[str] = mapped_column(String(1024))

    published_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    scraped_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
    first_seen_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    disappeared_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    discord_posted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    seller_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)
    category_slug: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, index=True
    )
    model_slug: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True, index=True
    )
    keyword_slugs: Mapped[Optional[list[Any]]] = mapped_column(JSONB, nullable=True)

    raw_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)

    photos: Mapped[list[Photo]] = relationship(
        back_populates="listing",
        cascade="all, delete-orphan",
        order_by="Photo.position",
    )
    observations: Mapped[list[ListingObservation]] = relationship(
        back_populates="listing",
        cascade="all, delete-orphan",
    )
    entities: Mapped[list[ListingEntity]] = relationship(
        back_populates="listing",
        cascade="all, delete-orphan",
    )


class Photo(Base):
    __tablename__ = "photos"
    __table_args__ = (UniqueConstraint("listing_id", "position", name="uq_photo_pos"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    listing_id: Mapped[int] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE"), index=True
    )
    url: Mapped[str] = mapped_column(String(2048))
    position: Mapped[int] = mapped_column(Integer, default=0)

    listing: Mapped[Listing] = relationship(back_populates="photos")


class ListingObservation(Base):
    __tablename__ = "listing_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    listing_id: Mapped[int] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE"), index=True
    )
    vinted_id: Mapped[int] = mapped_column(BigInteger, index=True)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    price_cents: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    is_present: Mapped[bool] = mapped_column(Boolean, default=True)
    title: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    brand: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    size: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    source_query: Mapped[Optional[str]] = mapped_column(
        String(512), nullable=True, index=True
    )
    raw_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    listing: Mapped[Listing] = relationship(back_populates="observations")


class MarketBrand(Base):
    __tablename__ = "market_brands"
    __table_args__ = (UniqueConstraint("slug", name="uq_market_brands_slug"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(128))
    display_name: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    models: Mapped[list[MarketModel]] = relationship(back_populates="brand")


class MarketModel(Base):
    __tablename__ = "market_models"
    __table_args__ = (UniqueConstraint("slug", name="uq_market_models_slug"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    brand_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("market_brands.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    slug: Mapped[str] = mapped_column(String(128))
    display_name: Mapped[str] = mapped_column(String(255))
    aliases: Mapped[Optional[list[Any]]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    brand: Mapped[Optional[MarketBrand]] = relationship(back_populates="models")


class MarketKeyword(Base):
    __tablename__ = "market_keywords"
    __table_args__ = (UniqueConstraint("slug", name="uq_market_keywords_slug"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(128))
    display_name: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(64), default="style")
    aliases: Mapped[Optional[list[Any]]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ListingEntity(Base):
    __tablename__ = "listing_entities"
    __table_args__ = (
        UniqueConstraint(
            "listing_id", "entity_type", "entity_slug", name="uq_listing_entity"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    listing_id: Mapped[int] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE"), index=True
    )
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_slug: Mapped[str] = mapped_column(String(128))
    confidence: Mapped[int] = mapped_column(Integer, default=100)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    listing: Mapped[Listing] = relationship(back_populates="entities")


class NicheSnapshot(Base):
    __tablename__ = "niche_snapshots"
    __table_args__ = (
        UniqueConstraint("niche_key", "window", name="uq_niche_snapshot_key_window"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    niche_key: Mapped[str] = mapped_column(String(512))
    window: Mapped[str] = mapped_column(String(8), index=True)
    brand_slug: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True, index=True
    )
    model_slug: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    category_slug: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    keyword_flags: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    listing_count: Mapped[int] = mapped_column(Integer, default=0)
    new_listings: Mapped[int] = mapped_column(Integer, default=0)
    disappeared_count: Mapped[int] = mapped_column(Integer, default=0)
    unique_sellers: Mapped[int] = mapped_column(Integer, default=0)
    price_min_cents: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    price_max_cents: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    price_mean_cents: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    price_median_cents: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    price_p25_cents: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    median_ttl_days: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    margin_proxy_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    score: Mapped[Optional[float]] = mapped_column(Float, nullable=True, index=True)
    metrics: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class OpportunityHistory(Base):
    """Historique des niches détectées — apprentissage des scores."""

    __tablename__ = "opportunity_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    niche_key: Mapped[str] = mapped_column(String(512), index=True)
    name: Mapped[str] = mapped_column(String(255))
    score: Mapped[float] = mapped_column(Float, index=True)
    lifecycle: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    niche_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    brand_slug: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    model_slug: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    category_slug: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    signals: Mapped[Optional[list[Any]]] = mapped_column(JSONB, nullable=True)
    payload: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    posted: Mapped[bool] = mapped_column(Boolean, default=False)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class TrendSnapshot(Base):
    """Historique quotidien des tendances détectées (analyse continue)."""

    __tablename__ = "trend_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_date",
            "entity_type",
            "entity_key",
            name="uq_trend_snapshot_day_entity",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_date: Mapped[date] = mapped_column(Date, index=True)
    entity_type: Mapped[str] = mapped_column(String(32), index=True)
    entity_key: Mapped[str] = mapped_column(String(255), index=True)
    display_name: Mapped[str] = mapped_column(String(255))
    strength: Mapped[float] = mapped_column(Float, index=True)
    direction: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    lifecycle: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    importance: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    recommendation: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    count_1d: Mapped[int] = mapped_column(Integer, default=0)
    count_7d: Mapped[int] = mapped_column(Integer, default=0)
    count_30d: Mapped[int] = mapped_column(Integer, default=0)
    count_90d: Mapped[int] = mapped_column(Integer, default=0)
    active_count: Mapped[int] = mapped_column(Integer, default=0)
    disappeared_7d: Mapped[int] = mapped_column(Integer, default=0)
    price_median_7d: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    price_median_30d: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    price_change_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    rotation_change_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    stock_change_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    popularity_change_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    continuation_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    gauge_growth: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    gauge_rentabilite: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    gauge_rarity: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    gauge_demand: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    gauge_saturation: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    triggers: Mapped[Optional[list[Any]]] = mapped_column(JSONB, nullable=True)
    payload: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ScrapeRun(Base):
    __tablename__ = "scrape_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), default="running")
    query: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    items_found: Mapped[int] = mapped_column(Integer, default=0)
    items_upserted: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class Checkpoint(Base):
    __tablename__ = "checkpoints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class MemberVintedAccount(Base):
    """Session Vinted Playwright liée à un membre Discord."""

    __tablename__ = "member_vinted_accounts"
    __table_args__ = (
        UniqueConstraint("discord_user_id", name="uq_member_vinted_discord_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    discord_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    discord_username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    vinted_username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    storage_state: Mapped[dict[str, Any]] = mapped_column(JSONB)
    linked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    autobuy_validated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class DiscordMemberPlan(Base):
    """Abonnement membre Discord (limites filtres privés)."""

    __tablename__ = "discord_member_plans"
    __table_args__ = (
        UniqueConstraint("discord_user_id", name="uq_discord_member_plan_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    discord_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    discord_username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    plan: Mapped[str] = mapped_column(String(32), default="starter")  # starter|premium|elite
    dm_channel_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    dm_dashboard_message_id: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class UserFilter(Base):
    """Filtre de recherche privé — visible uniquement par son propriétaire."""

    __tablename__ = "user_filters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    discord_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    brand: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    keyword: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    max_price_eur: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    min_price_eur: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class UserFilterAlert(Base):
    """Dédup : une alerte DM par filtre × annonce."""

    __tablename__ = "user_filter_alerts"
    __table_args__ = (
        UniqueConstraint("filter_id", "vinted_id", name="uq_user_filter_alert_once"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    filter_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user_filters.id", ondelete="CASCADE"), index=True
    )
    discord_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    vinted_id: Mapped[int] = mapped_column(BigInteger)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class VintedLinkToken(Base):
    """Lien temporaire Discord → page de connexion Vinted (self-service)."""

    __tablename__ = "vinted_link_tokens"
    __table_args__ = (UniqueConstraint("token", name="uq_vinted_link_token"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token: Mapped[str] = mapped_column(String(64), index=True)
    discord_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    discord_username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

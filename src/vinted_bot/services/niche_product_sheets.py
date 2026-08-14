"""Fiches produit niches — deep-dive premium des niches déjà validées par le détecteur.

Ne détecte aucune nouvelle niche : reprend uniquement les niches postées / historisées
par 🧠 Détecteur de niches, puis publie au plus 1 fiche / heure dans le salon dédié.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from vinted_bot.config import get_settings, sanitize_discord_channel_id
from vinted_bot.db.models import Listing, NicheSnapshot, OpportunityHistory
from vinted_bot.db.repositories import get_checkpoint, set_checkpoint
from vinted_bot.db.session import session_scope
from vinted_bot.notify.discord import DiscordNotifier, normalize_brand
from vinted_bot.services.market_embeds import (
    COLOR_GOLD,
    COLOR_GREEN,
    COLOR_ORANGE,
    COLOR_PURPLE,
    COLOR_RED,
    score_stars,
    _utcnow,
)
from vinted_bot.services.niche_insights import extract_depth_profile
from vinted_bot.services.opportunity_engine import (
    MIN_PUBLISH_LISTINGS,
    MIN_NICHE_SELLERS,
    Opportunity,
    PUBLISH_MIN_SCORE,
    _explore_search_query,
    _is_vague_search_token,
    _listing_matches_niche,
    _listing_url,
    _photo_candidates_from_listing,
    _radar_badges,
    _radar_demand_pct,
    _radar_sale_pct,
    _type_flags_from_keyword_flags,
    _vinted_explore_url,
    explore_search_variants,
    snapshot_to_opportunity,
)
from vinted_bot.utils.logging import get_logger

log = get_logger(__name__)

FICHES_POSTED_CHECKPOINT = "market:fiches:posted_keys"
FICHES_LAST_POST_CHECKPOINT = "market:fiches:last_post_at"
FICHES_SKIPPED_CHECKPOINT = "market:fiches:skipped_keys"
FICHES_PENDING_MSG_CHECKPOINT = "market:fiches:pending_msg"
FICHES_WAITING_MSG_CHECKPOINT = "market:fiches:waiting_msg"
FICHES_INTERVAL_SECONDS = 3600.0
# Deep-dive après sélection (défaut prod niches free ≈ 15–20 min via env)
FICHES_DEVELOP_SECONDS = 3600.0
FICHES_DEVELOP_FAST_SECONDS = 120.0
# Deep-dive réel : minimum de tours scrape + durée effective
MIN_DEVELOP_ROUNDS = 3
MIN_DEVELOP_ELAPSED_RATIO = 0.75
# Pause courte après un post réussi (le vrai plafond reste 1 fiche / heure)
FICHES_POST_GAP_SECONDS = 60.0
MIN_MOSAIC_PHOTOS = 6
MAX_MOSAIC_PHOTOS = 10  # limite Discord embeds / message
# Ne jamais oublier une fiche déjà postée (anti-repost)
MAX_POSTED_FICHES_KEPT = 5000
# Niches examinées (échec / inéligible) : ne pas re-brûler pendant longtemps
MAX_SKIPPED_FICHES_KEPT = 2000
FICHES_SKIP_TTL_HOURS = 720.0  # 30 jours
MIN_FICHE_LISTINGS = max(MIN_PUBLISH_LISTINGS, 8)
MIN_FICHE_SELLERS = max(MIN_NICHE_SELLERS, 2)
MIN_FICHE_SCORE = PUBLISH_MIN_SCORE
# Après deep-dive le score peut frôler le seuil — tolérance pour niches déjà validées
MIN_FICHE_SCORE_AFTER_DEVELOP = max(60.0, PUBLISH_MIN_SCORE - 5.0)

# Frais Vinted FR (approximation réelle acheteur)
_BUYER_PROTECTION_RATE = 0.05
_BUYER_PROTECTION_FIXED_EUR = 0.70
_DEFAULT_SHIPPING_EUR = 3.99


@dataclass(frozen=True, slots=True)
class MosaicItem:
    photo_url: str
    listing_url: str
    seller_key: str
    price_eur: float | None
    title: str


@dataclass(frozen=True, slots=True)
class NicheProductSheet:
    opportunity: Opportunity
    mosaic: tuple[MosaicItem, ...]
    look_for: tuple[str, ...]
    avoid: tuple[str, ...]
    ai_analysis: str
    buy_article_eur: float | None
    buy_landed_eur: float | None  # article + protection + port
    resell_eur: float | None
    shipping_estimate_eur: float
    volume_analyzed: int
    developed_minutes: int


def _channel_fiches(settings: Any | None = None) -> str:
    s = settings or get_settings()
    raw = getattr(s, "discord_channel_fiches_produit", "") or ""
    return sanitize_discord_channel_id(str(raw))


def _validate_fiches_post_channel(settings: Any, channel: str) -> str | None:
    """Refuse les mauvais salons (détecteur, niches vinted catalogue)."""
    if not channel:
        return "channel_or_token_missing"
    niches_channel = sanitize_discord_channel_id(
        getattr(settings, "discord_channel_niches", "") or ""
    )
    niches_vinted = sanitize_discord_channel_id(
        getattr(settings, "discord_channel_niches_vinted", "") or ""
    )
    if niches_channel and channel == niches_channel:
        log.error("fiche_refused_same_as_niches_channel", channel=channel)
        return "fiches_channel_equals_niches"
    if niches_vinted and channel == niches_vinted:
        log.error("fiche_refused_niches_vinted_catalog_channel", channel=channel)
        return "fiches_channel_equals_niches_vinted"
    return None


def _develop_meets_minimum(
    summary: dict[str, Any],
    *,
    target_seconds: float,
    fast: bool,
) -> bool:
    """Vérifie que le deep-dive a réellement tourné (pas un skip silencieux)."""
    rounds = int(summary.get("rounds") or 0)
    elapsed = int(summary.get("elapsed_s") or 0)
    if fast:
        return rounds >= 1 and elapsed >= 30
    if rounds < MIN_DEVELOP_ROUNDS:
        return False
    need_elapsed = max(120.0, target_seconds * MIN_DEVELOP_ELAPSED_RATIO)
    return elapsed >= need_elapsed


def _effective_develop_seconds(requested: float | None) -> float:
    """Cap deep-dive pour tenir le rythme 1 fiche / heure + phase detector."""
    settings = get_settings()
    base = float(
        requested
        if requested is not None
        else getattr(settings, "fiches_develop_seconds", None) or FICHES_DEVELOP_SECONDS
    )
    # Laisse ~40 min / heure au détecteur (RAM + cadence ~10 détections)
    cap = max(300.0, min(1200.0, FICHES_INTERVAL_SECONDS - 2100.0))
    # Si env demande plus long (service fiches dédié), autorise jusqu'à intervalle-3min
    dedicated_cap = max(300.0, FICHES_INTERVAL_SECONDS - 180.0)
    if base >= 1800.0:
        cap = dedicated_cap
    return min(max(30.0, base), cap)


def build_fiche_waiting_payload() -> dict[str, Any]:
    return {
        "embeds": [
            {
                "title": "⏳ Prochaine fiche produit",
                "description": (
                    "En attente d'une niche **validée** par 🧠 **Détecteur de niches**.\n\n"
                    "Dès qu'une opportunité est éligible, le deep-dive démarre "
                    "et une fiche sera publiée **ici**.\n\n"
                    "⏱️ **1 fiche maximum par heure** — jamais la même niche deux fois."
                )[:3900],
                "color": 0x5865F2,
                "footer": {"text": "Fiches produit niches · Resello"},
            }
        ]
    }


def _load_status_message(checkpoint_key: str) -> dict[str, str]:
    with session_scope() as session:
        data = get_checkpoint(session, checkpoint_key) or {}
    if not isinstance(data, dict):
        return {}
    channel_id = sanitize_discord_channel_id(str(data.get("channel_id") or ""))
    message_id = str(data.get("message_id") or "").strip()
    if channel_id and message_id:
        return {"channel_id": channel_id, "message_id": message_id}
    return {}


def _save_status_message(
    checkpoint_key: str,
    *,
    channel_id: str,
    message_id: str,
    extra: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "channel_id": channel_id,
        "message_id": message_id,
    }
    if extra:
        payload.update(extra)
    with session_scope() as session:
        set_checkpoint(session, checkpoint_key, payload)


def _clear_status_message(
    notifier: DiscordNotifier,
    checkpoint_key: str,
) -> None:
    meta = _load_status_message(checkpoint_key)
    if not meta:
        return
    try:
        notifier.delete_message(meta["channel_id"], meta["message_id"])
    except Exception as exc:  # noqa: BLE001
        log.debug("fiche_status_delete_skipped", key=checkpoint_key, error=str(exc)[:80])
    with session_scope() as session:
        set_checkpoint(session, checkpoint_key, {})


def _upsert_status_message(
    notifier: DiscordNotifier,
    checkpoint_key: str,
    channel_id: str,
    payload: dict[str, Any],
    *,
    extra: dict[str, Any] | None = None,
) -> None:
    meta = _load_status_message(checkpoint_key)
    if meta.get("channel_id") == channel_id and meta.get("message_id"):
        try:
            notifier.edit_message(channel_id, meta["message_id"], payload)
            return
        except Exception as exc:  # noqa: BLE001
            log.debug("fiche_status_edit_failed", error=str(exc)[:120])
    data = notifier.post_message(channel_id, payload)
    message_id = str(data.get("id") or "")
    if message_id:
        _save_status_message(
            checkpoint_key,
            channel_id=channel_id,
            message_id=message_id,
            extra=extra,
        )


def _maybe_post_waiting_status(
    notifier: DiscordNotifier,
    channel_id: str,
    *,
    reason: str,
) -> None:
    if reason != "waiting_for_detector":
        _clear_status_message(notifier, FICHES_WAITING_MSG_CHECKPOINT)
        return
    if _load_status_message(FICHES_PENDING_MSG_CHECKPOINT):
        return
    _upsert_status_message(
        notifier,
        FICHES_WAITING_MSG_CHECKPOINT,
        channel_id,
        build_fiche_waiting_payload(),
    )


def _clear_fiche_pending(notifier: DiscordNotifier, channel_id: str) -> None:
    _clear_status_message(notifier, FICHES_PENDING_MSG_CHECKPOINT)
    _clear_status_message(notifier, FICHES_WAITING_MSG_CHECKPOINT)


def _load_posted_fiche_keys() -> dict[str, str]:
    with session_scope() as session:
        data = get_checkpoint(session, FICHES_POSTED_CHECKPOINT) or {}
    keys = data.get("keys") if isinstance(data, dict) else None
    return dict(keys) if isinstance(keys, dict) else {}


def _mark_fiche_posted(niche_key: str) -> None:
    now = _utcnow().isoformat()
    with session_scope() as session:
        data = get_checkpoint(session, FICHES_POSTED_CHECKPOINT) or {}
        keys = dict(data.get("keys") or {}) if isinstance(data, dict) else {}
        keys[niche_key] = now
        # trim
        if len(keys) > MAX_POSTED_FICHES_KEPT:
            ordered = sorted(keys.items(), key=lambda kv: kv[1], reverse=True)
            keys = dict(ordered[:MAX_POSTED_FICHES_KEPT])
        set_checkpoint(session, FICHES_POSTED_CHECKPOINT, {"keys": keys})
        set_checkpoint(
            session,
            FICHES_LAST_POST_CHECKPOINT,
            {"at": now},
        )
        # Ne plus retenir comme skip si on a réussi à poster
        skip_data = get_checkpoint(session, FICHES_SKIPPED_CHECKPOINT) or {}
        skip_keys = dict(skip_data.get("keys") or {}) if isinstance(skip_data, dict) else {}
        if niche_key in skip_keys:
            skip_keys.pop(niche_key, None)
            set_checkpoint(session, FICHES_SKIPPED_CHECKPOINT, {"keys": skip_keys})


def _load_skipped_fiche_keys() -> dict[str, str]:
    """Niches qui ont échoué au build fiche récemment (évite de re-brûler 1 h)."""
    cutoff = _utcnow() - timedelta(hours=FICHES_SKIP_TTL_HOURS)
    with session_scope() as session:
        data = get_checkpoint(session, FICHES_SKIPPED_CHECKPOINT) or {}
    raw = data.get("keys") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return {}
    kept: dict[str, str] = {}
    for key, at in raw.items():
        if not key or not isinstance(at, str):
            continue
        try:
            ts = datetime.fromisoformat(at.replace("Z", "+00:00"))
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts >= cutoff:
            kept[str(key)] = at
    return kept


def _mark_fiche_skipped(niche_key: str, *, reason: str) -> None:
    now = _utcnow().isoformat()
    with session_scope() as session:
        data = get_checkpoint(session, FICHES_SKIPPED_CHECKPOINT) or {}
        keys = dict(data.get("keys") or {}) if isinstance(data, dict) else {}
        keys[niche_key] = now
        if len(keys) > MAX_SKIPPED_FICHES_KEPT:
            ordered = sorted(keys.items(), key=lambda kv: kv[1], reverse=True)
            keys = dict(ordered[:MAX_SKIPPED_FICHES_KEPT])
        set_checkpoint(session, FICHES_SKIPPED_CHECKPOINT, {"keys": keys})
    log.info("fiche_niche_skipped", niche_key=niche_key, reason=reason)


def hours_since_last_fiche() -> float | None:
    with session_scope() as session:
        data = get_checkpoint(session, FICHES_LAST_POST_CHECKPOINT) or {}
    raw = data.get("at") if isinstance(data, dict) else None
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (_utcnow() - dt).total_seconds() / 3600.0)


def fiche_cooldown_remaining_seconds(*, develop_paced: bool = False) -> float:
    """Toujours 1 fiche / heure max (develop_paced n'accélère plus le plafond)."""
    del develop_paced  # conservé pour compat signature CLI / loop
    elapsed_h = hours_since_last_fiche()
    if elapsed_h is None:
        return 0.0
    remaining = FICHES_INTERVAL_SECONDS - elapsed_h * 3600.0
    return max(0.0, remaining)


def _validated_niche_keys(
    *,
    lookback_hours: float | None = None,
) -> list[tuple[str, float, str]]:
    """Niches déjà POSTÉES par le 🧠 détecteur, pas encore fichées.

    Source : historique ``opportunity_history`` (posted=True) + checkpoint
    ``market:opp:posted_keys`` (filet si la row a > lookback / trim).
    Pas de fenêtre 7j : sinon une niche détectée mais jamais fichée est perdue.
    """
    from vinted_bot.services.opportunity_engine import _load_recently_posted_keys

    with session_scope() as session:
        stmt = (
            select(OpportunityHistory)
            .where(OpportunityHistory.posted.is_(True))
            .order_by(
                OpportunityHistory.score.desc(),
                OpportunityHistory.detected_at.desc(),
            )
            .limit(500)
        )
        if lookback_hours is not None and lookback_hours > 0:
            cutoff = _utcnow() - timedelta(hours=float(lookback_hours))
            stmt = stmt.where(OpportunityHistory.detected_at >= cutoff)
        rows = list(session.scalars(stmt).all())
        materialised = [
            (
                (row.niche_key or "").strip(),
                float(row.score or 0.0),
                (row.name or row.niche_key or "")[:120],
            )
            for row in rows
        ]

    best: dict[str, tuple[float, str]] = {}
    for key, score, name in materialised:
        if not key:
            continue
        prev = best.get(key)
        if prev is None or score > prev[0]:
            best[key] = (score, name)

    # Filet : clés détecteur postées (checkpoint) absentes de l'historique chargé
    for key, _at in _load_recently_posted_keys().items():
        nk = str(key or "").strip()
        if not nk or nk in best:
            continue
        snap = _snapshot_for_key(nk)
        score = float(getattr(snap, "score", None) or 0.0) if snap else 0.0
        name = nk
        if snap is not None:
            bits = [
                getattr(snap, "brand_slug", None),
                getattr(snap, "model_slug", None),
                getattr(snap, "category_slug", None),
            ]
            label = " ".join(str(b) for b in bits if b).strip()
            if label:
                name = label[:120]
        best[nk] = (score, name)

    # Exclure dès la source les fiches déjà postées (évite churn pick)
    already_fiched = _load_posted_fiche_keys()
    ordered = sorted(
        ((k, sc, name) for k, (sc, name) in best.items() if k not in already_fiched),
        key=lambda kv: kv[1],
        reverse=True,
    )
    return [(k, sc, name) for k, sc, name in ordered]


def _buyer_protection_eur(article_eur: float) -> float:
    return round(article_eur * _BUYER_PROTECTION_RATE + _BUYER_PROTECTION_FIXED_EUR, 2)


def _shipping_cents_from_raw(raw: dict[str, Any]) -> int | None:
    """Extrait frais de port si présents dans le JSON Vinted."""
    for key in (
        "shipping_price",
        "shipment_price",
        "delivery_price",
        "postage",
        "shipping",
    ):
        val = raw.get(key)
        if isinstance(val, dict) and val.get("amount") is not None:
            try:
                return int(round(float(val["amount"]) * 100))
            except (TypeError, ValueError):
                pass
        if isinstance(val, (int, float)) and val > 0:
            # déjà en cents si grand, sinon euros
            if val > 50:
                return int(val)
            return int(round(float(val) * 100))
    return None


def _landed_buy_eur_from_listing(listing: Listing) -> tuple[float | None, float | None, float]:
    """(prix article €, prix tout compris €, port estimé €)."""
    raw = listing.raw_json if isinstance(listing.raw_json, dict) else {}
    article: float | None = None
    if listing.price_cents is not None:
        article = listing.price_cents / 100.0
    total = raw.get("total_item_price")
    protected: float | None = None
    if isinstance(total, dict) and total.get("amount") is not None:
        try:
            protected = float(total["amount"])
        except (TypeError, ValueError):
            protected = None
    if article is None and protected is not None:
        # total_item_price ≈ article + protection (sans port)
        article = max(0.0, protected - _buyer_protection_eur(protected / 1.05))
    if article is None:
        return None, None, _DEFAULT_SHIPPING_EUR

    if protected is None:
        protected = article + _buyer_protection_eur(article)

    ship_cents = _shipping_cents_from_raw(raw)
    ship_eur = (ship_cents / 100.0) if ship_cents else _DEFAULT_SHIPPING_EUR
    landed = round(protected + ship_eur, 2)
    return round(article, 2), landed, round(ship_eur, 2)


def _market_prices_with_fees(
    snap: NicheSnapshot,
    op: Opportunity,
) -> tuple[float | None, float | None, float | None, float]:
    """Moyennes article / landed / revente + port moyen estimé."""
    landed_vals: list[float] = []
    article_vals: list[float] = []
    ship_vals: list[float] = []
    with session_scope() as session:
        stmt = (
            select(Listing)
            .where(Listing.is_active.is_(True))
            .order_by(Listing.last_seen_at.desc().nullslast())
            .limit(120)
        )
        rows = list(session.scalars(stmt).all())
        matched = [L for L in rows if _listing_matches_niche(L, snap)]
        for L in matched[:60]:
            art, landed, ship = _landed_buy_eur_from_listing(L)
            if art is not None:
                article_vals.append(art)
            if landed is not None:
                landed_vals.append(landed)
            ship_vals.append(ship)

    ship_avg = (
        round(sum(ship_vals) / len(ship_vals), 2) if ship_vals else _DEFAULT_SHIPPING_EUR
    )
    if article_vals:
        article_avg = round(sorted(article_vals)[len(article_vals) // 4], 2)  # ~P25
    else:
        article_avg = op.price_buy_avg_eur
    if landed_vals:
        landed_avg = round(sorted(landed_vals)[len(landed_vals) // 4], 2)
    elif article_avg is not None:
        landed_avg = round(
            article_avg + _buyer_protection_eur(article_avg) + ship_avg, 2
        )
    else:
        landed_avg = None
    resell = op.price_resell_avg_eur
    return article_avg, landed_avg, resell, ship_avg


def develop_niche(
    op: Opportunity,
    *,
    duration_seconds: float | None = None,
    headless: bool = True,
    fast: bool = False,
) -> dict[str, Any]:
    """Re-analyse / enrichit UNE niche validée pendant ~1 h (scrapes ciblés)."""
    settings = get_settings()
    duration = float(
        duration_seconds
        if duration_seconds is not None
        else (
            FICHES_DEVELOP_FAST_SECONDS
            if fast
            else float(
                getattr(settings, "fiches_develop_seconds", None)
                or FICHES_DEVELOP_SECONDS
            )
        )
    )
    duration = max(30.0, duration)
    variants = explore_search_variants(op)
    if not variants:
        variants = [_explore_search_query(op) or op.name]

    log.info(
        "fiche_develop_start",
        niche_key=op.niche_key,
        name=op.name,
        duration_s=int(duration),
        queries=variants,
    )

    from vinted_bot.clients.vinted_browser import vinted_browser
    from vinted_bot.services.niche_detector import (
        _fetch_probe_items,
        persist_probe_items,
    )
    from vinted_bot.niche_config import load_niches_config

    cfg = load_niches_config()
    catalog_ids = list(cfg.catalog_ids or [])
    # Horloge murale : monotonic ne compte pas le sommeil Mac → deep-dive
    # qui s'étirait sur 20h+ sans jamais poster.
    started = time.time()
    rounds = 0
    saved_total = 0
    # Espacer les tours pour couvrir ~duration (min ~45s entre tours)
    interval = max(45.0, min(300.0, duration / 8.0))

    try:
        with vinted_browser(
            base_url=settings.vinted_base_url,
            headless=headless,
            delay_seconds=settings.request_delay_seconds,
        ) as browser:
            browser.warm_up()
            while True:
                elapsed = time.time() - started
                if elapsed >= duration:
                    break
                q = variants[rounds % len(variants)]
                try:
                    items = _fetch_probe_items(
                        browser,
                        query=q,
                        catalog_ids=catalog_ids,
                        max_items=36,
                        base_url=settings.vinted_base_url,
                    )
                    saved = persist_probe_items(items, source_query=f"fiche:{q}")
                    saved_total += saved
                    rounds += 1
                    log.info(
                        "fiche_develop_round",
                        round=rounds,
                        query=q,
                        items=len(items),
                        saved=saved,
                        elapsed_s=int(elapsed),
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "fiche_develop_round_failed",
                        query=q,
                        error=str(exc)[:160],
                    )
                    rounds += 1
                elapsed = time.time() - started
                remaining = duration - elapsed
                if remaining <= 5:
                    break
                time.sleep(min(interval, remaining))
    except Exception as exc:  # noqa: BLE001
        log.warning("fiche_develop_browser_failed", error=str(exc)[:200])

    # Recalcule agrégats marché (sans poster le radar niches)
    try:
        from vinted_bot.services.market_intel import run_market_intel_cycle

        intel = run_market_intel_cycle(
            post_discord=False,
            reconcile=False,
            force_discord=False,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("fiche_develop_intel_failed", error=str(exc)[:160])
        intel = {"error": str(exc)[:120]}

    elapsed_final = time.time() - started
    summary = {
        "niche_key": op.niche_key,
        "rounds": rounds,
        "saved": saved_total,
        "elapsed_s": int(elapsed_final),
        "intel": intel,
    }
    log.info("fiche_develop_done", **{k: v for k, v in summary.items() if k != "intel"})
    return summary


def _snapshot_for_key(niche_key: str) -> NicheSnapshot | None:
    with session_scope() as session:
        # Prefer 7d window then any
        for window in ("7d", "30d", "1d", "14d"):
            row = session.scalar(
                select(NicheSnapshot).where(
                    NicheSnapshot.niche_key == niche_key,
                    NicheSnapshot.window == window,
                )
            )
            if row is not None:
                session.expunge(row)
                return row
        row = session.scalar(
            select(NicheSnapshot)
            .where(NicheSnapshot.niche_key == niche_key)
            .order_by(NicheSnapshot.listing_count.desc())
            .limit(1)
        )
        if row is not None:
            session.expunge(row)
        return row


def _collect_mosaic(snap: NicheSnapshot) -> list[MosaicItem]:
    """6–10 annonces / photos de vendeurs différents pour la niche."""
    with session_scope() as session:
        stmt = (
            select(Listing)
            .options(selectinload(Listing.photos))
            .where(Listing.is_active.is_(True))
            .order_by(Listing.last_seen_at.desc().nullslast())
            .limit(220)
        )
        if snap.model_slug:
            model_like = f"%{snap.model_slug.replace('_', '%')}%"
            stmt = stmt.where(
                or_(
                    Listing.model_slug == snap.model_slug,
                    Listing.title.ilike(model_like),
                )
            )
        elif snap.category_slug:
            stmt = stmt.where(Listing.category_slug == snap.category_slug)
        elif snap.brand_slug:
            stmt = stmt.where(Listing.brand.ilike(f"%{snap.brand_slug.replace('_', '%')}%"))

        rows = list(session.scalars(stmt).unique().all())
        matched = [L for L in rows if _listing_matches_niche(L, snap)]
        if len(matched) < MIN_MOSAIC_PHOTOS and snap.model_slug:
            # Pas d'élargissement marque-seule : photos doivent coller au modèle analysé
            log.info(
                "fiche_mosaic_strict",
                niche_key=snap.niche_key,
                matched=len(matched),
                need=MIN_MOSAIC_PHOTOS,
            )

        # Diversité vendeurs + prix
        by_seller: dict[str, list[Listing]] = {}
        for L in matched:
            raw = L.raw_json if isinstance(L.raw_json, dict) else {}
            user = raw.get("user") if isinstance(raw.get("user"), dict) else {}
            seller = str(
                user.get("login")
                or user.get("id")
                or getattr(L, "seller_id", None)
                or L.vinted_id
                or id(L)
            )
            by_seller.setdefault(seller, []).append(L)

        # Un listing par vendeur en priorité, puis compléter
        picks: list[Listing] = []
        leftovers: list[Listing] = []
        for seller, items in by_seller.items():
            items_sorted = sorted(
                items,
                key=lambda x: (x.price_cents is None, x.price_cents or 0),
            )
            picks.append(items_sorted[0])
            leftovers.extend(items_sorted[1:])
        # Varier les prix : mélanger bas / médian / haut
        picks.sort(key=lambda x: x.price_cents or 0)
        if len(picks) >= 3:
            mid = len(picks) // 2
            diversified = []
            lo, hi = 0, len(picks) - 1
            while lo <= hi and len(diversified) < MAX_MOSAIC_PHOTOS:
                diversified.append(picks[lo])
                lo += 1
                if lo <= hi and len(diversified) < MAX_MOSAIC_PHOTOS:
                    diversified.append(picks[hi])
                    hi -= 1
                if lo <= hi and lo == mid and len(diversified) < MAX_MOSAIC_PHOTOS:
                    diversified.append(picks[mid])
                    lo = mid + 1
            picks = list(dict.fromkeys(diversified))
        for L in leftovers:
            if len(picks) >= MAX_MOSAIC_PHOTOS:
                break
            if L not in picks:
                picks.append(L)

        mosaic: list[MosaicItem] = []
        seen_photos: set[str] = set()
        for L in picks:
            if len(mosaic) >= MAX_MOSAIC_PHOTOS:
                break
            photos = _photo_candidates_from_listing(L)
            if not photos:
                continue
            photo = photos[0]
            if photo in seen_photos:
                continue
            seen_photos.add(photo)
            raw = L.raw_json if isinstance(L.raw_json, dict) else {}
            user = raw.get("user") if isinstance(raw.get("user"), dict) else {}
            seller = str(user.get("login") or user.get("id") or L.vinted_id)
            price = (L.price_cents / 100.0) if L.price_cents is not None else None
            mosaic.append(
                MosaicItem(
                    photo_url=photo,
                    listing_url=_listing_url(L),
                    seller_key=seller,
                    price_eur=price,
                    title=(L.title or "")[:80],
                )
            )
        return mosaic


def _vinted_explore_url_from_snap(snap: NicheSnapshot) -> str:
    from urllib.parse import urlencode

    base = (get_settings().vinted_base_url or "https://www.vinted.fr").rstrip("/")
    parts: list[str] = []
    if snap.brand_slug and snap.brand_slug.lower() not in {"inconnu", "unknown"}:
        parts.append(snap.brand_slug.replace("_", " "))
    if snap.model_slug:
        parts.append(snap.model_slug.replace("_", " "))
    for flag in _type_flags_from_keyword_flags(snap.keyword_flags or ""):
        parts.append(flag)
    q = " ".join(dict.fromkeys(parts)).strip()
    if not q or _is_vague_search_token(q):
        q = " ".join(parts[:2]).strip() or "vinted"
    return f"{base}/catalog?{urlencode([('search_text', q), ('order', 'newest_first')])}"


def _look_for_lines(op: Opportunity, titles: Sequence[str]) -> tuple[str, ...]:
    lines: list[str] = []
    depth = extract_depth_profile(list(titles))
    if op.brand_slug and op.brand_slug.lower() not in {"inconnu", "unknown"}:
        lines.append(f"Marque : {op.brand_slug.replace('_', ' ').title()}")
    if op.model_slug:
        lines.append(f"Modèle / type : {op.model_slug.replace('_', ' ')}")
    for flag in _type_flags_from_keyword_flags(op.keyword_flags):
        lines.append(f"Segment : {flag.title()}")
    if depth.years:
        lines.append("Années / éditions : " + ", ".join(str(y) for y in depth.years[:4]))
    if depth.has_collab:
        lines.append("Collabs / éditions limitées")
    if depth.has_edition:
        lines.append("Éditions spéciales / OG / limited")
    for v in (op.depth_summary or "").split("·"):
        v = v.strip()
        if not v or len(v) <= 3 or v.startswith("peu de"):
            continue
        if v.lower().startswith("couleur"):
            continue
        lines.append(v)
    for t in op.search_terms[:6]:
        tl = (t or "").strip()
        if tl and not _is_vague_search_token(tl) and tl.lower() not in {x.lower() for x in lines}:
            lines.append(f"Mot-clé : {tl}")
    if op.model_slug:
        lines.append(f"Modèle : {op.model_slug.replace('_', ' ')}")
    # dédup
    out: list[str] = []
    seen: set[str] = set()
    for line in lines:
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(line)
        if len(out) >= 8:
            break
    if not out:
        out = [f"Rechercher « {_explore_search_query(op)} »", "Variantes propres, photos nettes"]
    return tuple(out)


def _avoid_lines(op: Opportunity) -> tuple[str, ...]:
    lines: list[str] = []
    if op.lifecycle_avoid:
        lines.append("Cycle avancé / saturation — éviter de stocker trop longtemps")
    if op.lifecycle in {"decline", "saturated"}:
        lines.append(f"Cycle « {op.lifecycle_label or op.lifecycle} » — prudence")
    if op.competition_score >= 70:
        lines.append("Forte concurrence vendeurs — négocier serré")
    if op.confidence < 55:
        lines.append("Confiance d’analyse moyenne — vérifier plusieurs annonces")
    if op.weak_signal:
        lines.append("Signal encore précoce — volume à confirmer")
    if (op.margin_pct or 0) < 20:
        lines.append("Marges serrées — éviter les défauts / mauvais états")
    lines.append("Éviter les lots douteux, contrefaçons et photos trop floues")
    lines.append("Ne pas surpayer au-dessus du prix d’achat observé")
    return tuple(dict.fromkeys(lines))[:6]


def _ai_analysis_block(op: Opportunity) -> str:
    chunks: list[str] = []
    if op.explain_why:
        chunks.append(op.explain_why.strip())
    elif op.why_short:
        chunks.append(op.why_short.strip())
    if op.explain_signals:
        chunks.append(op.explain_signals.strip())
    elif op.signals:
        chunks.append("Signaux : " + " · ".join(op.signals[:3]))
    if op.explain_strategy:
        chunks.append(op.explain_strategy.strip())
    elif op.strategy_buy or op.action_detail:
        chunks.append((op.strategy_buy or op.action_detail or "").strip())
    # Pourquoi maintenant
    if op.lifecycle_label:
        chunks.append(f"Pourquoi maintenant : {op.lifecycle_label.strip()}.")
    elif op.angle_emerging >= 55:
        chunks.append("Pourquoi maintenant : la niche montre encore une dynamique de croissance.")
    else:
        chunks.append(
            "Pourquoi maintenant : indicateurs demande / rotation favorables sur l’échantillon analysé."
        )
    chunks.append(
        "Pour les revendeurs : niche déjà validée par le détecteur — utile si vous trouvez "
        "sous le prix d’achat observé avec un état revente propre."
    )
    # 4–6 lignes max, pas de jargon
    lines: list[str] = []
    for c in chunks:
        for part in c.replace("\n", " ").split(". "):
            p = part.strip().rstrip(".")
            if len(p) < 20:
                continue
            lines.append(p + ".")
            if len(lines) >= 6:
                break
        if len(lines) >= 6:
            break
    while len(lines) < 4:
        lines.append(
            "Échantillon multi-vendeurs confirmé — traiter comme une étude de marché, pas une annonce isolée."
        )
    return "\n".join(lines[:6])


def build_niche_product_sheet(
    op: Opportunity,
    *,
    snap: NicheSnapshot | None = None,
    min_score: float | None = None,
) -> NicheProductSheet | None:
    """Construit une fiche premium si l’échantillon marché est solide."""
    score_floor = float(MIN_FICHE_SCORE if min_score is None else min_score)
    if op.listing_count < MIN_FICHE_LISTINGS or op.unique_sellers < MIN_FICHE_SELLERS:
        return None
    if op.score < score_floor:
        return None

    snapshot = snap
    if snapshot is None:
        snapshot = _snapshot_for_key(op.niche_key)
    if snapshot is None:
        return None

    mosaic = _collect_mosaic(snapshot)
    if len(mosaic) < MIN_MOSAIC_PHOTOS:
        log.info(
            "fiche_skipped_thin_mosaic",
            niche_key=op.niche_key,
            mosaic=len(mosaic),
            need=MIN_MOSAIC_PHOTOS,
        )
        return None

    titles = [m.title for m in mosaic if m.title]
    article, landed, resell, ship = _market_prices_with_fees(snapshot, op)
    return NicheProductSheet(
        opportunity=op,
        mosaic=tuple(mosaic[:MAX_MOSAIC_PHOTOS]),
        look_for=_look_for_lines(op, titles),
        avoid=_avoid_lines(op),
        ai_analysis=_ai_analysis_block(op),
        buy_article_eur=article,
        buy_landed_eur=landed,
        resell_eur=resell,
        shipping_estimate_eur=ship,
        volume_analyzed=max(op.listing_count, op.sample_size, len(mosaic)),
        developed_minutes=0,
    )


def _sheet_failure_reason(
    op: Opportunity | None,
    snap: NicheSnapshot | None,
    *,
    min_score: float | None = None,
) -> str:
    score_floor = float(MIN_FICHE_SCORE if min_score is None else min_score)
    if op is None:
        return "opportunity_unavailable"
    if snap is None:
        return "snapshot_missing"
    if op.listing_count < MIN_FICHE_LISTINGS:
        return f"listings_low:{op.listing_count}"
    if op.unique_sellers < MIN_FICHE_SELLERS:
        return f"sellers_low:{op.unique_sellers}"
    if op.score < score_floor:
        return f"score_low:{op.score:.1f}<{score_floor:.0f}"
    mosaic = _collect_mosaic(snap)
    if len(mosaic) < MIN_MOSAIC_PHOTOS:
        return f"mosaic_thin:{len(mosaic)}<{MIN_MOSAIC_PHOTOS}"
    return "unknown"


def _with_developed_minutes(
    sheet: NicheProductSheet, developed_minutes: int
) -> NicheProductSheet:
    return NicheProductSheet(
        opportunity=sheet.opportunity,
        mosaic=sheet.mosaic,
        look_for=sheet.look_for,
        avoid=sheet.avoid,
        ai_analysis=sheet.ai_analysis,
        buy_article_eur=sheet.buy_article_eur,
        buy_landed_eur=sheet.buy_landed_eur,
        resell_eur=sheet.resell_eur,
        shipping_estimate_eur=sheet.shipping_estimate_eur,
        volume_analyzed=sheet.volume_analyzed,
        developed_minutes=developed_minutes,
    )


def build_fiche_discord_payload(sheet: NicheProductSheet) -> dict[str, Any]:
    """Payload Discord : mosaïque collage + fiche (même url → collage)."""
    op = sheet.opportunity
    color = {
        "exceptional": COLOR_RED,
        "strong": COLOR_GOLD,
        "interesting": COLOR_GREEN,
        "hidden": COLOR_PURPLE,
    }.get(op.priority, COLOR_ORANGE)
    if op.niche_type == "hidden":
        color = COLOR_PURPLE

    explore = _vinted_explore_url(op)
    ref = sheet.mosaic[0] if sheet.mosaic else None
    ref_url = (op.photo_listing_url or (ref.listing_url if ref else "") or explore).strip()
    ref_title = (op.photo_listing_title or (ref.title if ref else "") or "").strip()
    badges = list(_radar_badges(op))
    if "💎 Opportunité validée" not in badges:
        badges = ["💎 Opportunité validée", *badges][:4]
    badge_line = "\n".join(badges)
    buy_article = (
        f"{sheet.buy_article_eur:.0f} €" if sheet.buy_article_eur is not None else "—"
    )
    buy_landed = (
        f"{sheet.buy_landed_eur:.0f} €" if sheet.buy_landed_eur is not None else "—"
    )
    resell = f"{sheet.resell_eur:.0f} €" if sheet.resell_eur is not None else "—"
    demand = _radar_demand_pct(op)
    sale = _radar_sale_pct(op)
    volume = sheet.volume_analyzed
    look = "\n".join(f"• {x}" for x in sheet.look_for) or "• Voir mots-clés ci-dessous"
    avoid = "\n".join(f"• {x}" for x in sheet.avoid)
    search_q = _explore_search_query(op)
    develop_note = (
        f"Deep-dive {sheet.developed_minutes} min"
        if sheet.developed_minutes > 0
        else "Niche validée détecteur"
    )

    description = (
        f"**{op.name}**\n\n"
        f"⭐ Opportunité : **{op.score:.0f}/100**\n"
        f"{score_stars(op.score)}\n\n"
        f"{badge_line}"
    )
    if ref_url and ref_url != explore:
        description += (
            f"\n\n📷 **[Produit de référence]({ref_url})**"
            + (f" — _{ref_title[:90]}_" if ref_title else "")
        )

    fields = [
        {
            "name": "💰 Marché",
            "value": (
                f"Prix d'achat (article) :\n**{buy_article}**\n\n"
                f"Prix d'achat tout compris* :\n**{buy_landed}**\n"
                f"_\\*protection acheteur + port ~{sheet.shipping_estimate_eur:.2f} €_\n\n"
                f"Prix de revente moyen :\n**{resell}**\n\n"
                f"Volume analysé :\n**{volume} annonces**"
            )[:1024],
            "inline": False,
        },
        {
            "name": "📊 Signaux",
            "value": (
                f"🔥 Demande : **+{demand} %**\n"
                f"⚡ Vente : **+{sale} %**"
            )[:1024],
            "inline": False,
        },
        {
            "name": "🧠 Analyse IA",
            "value": sheet.ai_analysis[:1024],
            "inline": False,
        },
        {
            "name": "🔍 Ce qu'il faut rechercher",
            "value": look[:1024],
            "inline": False,
        },
        {
            "name": "⚠️ À éviter",
            "value": avoid[:1024],
            "inline": False,
        },
        {
            "name": "🔗 Explorer la niche",
            "value": (
                f"[Ouvrir le catalogue « {search_q} »]({explore})"
            )[:1024],
            "inline": False,
        },
    ]

    # Premier embed = fiche + 1re image ; suivants = collage (même url)
    photos = [m.photo_url for m in sheet.mosaic]
    mosaic_links = sheet.mosaic
    main: dict[str, Any] = {
        "title": f"📊 FICHE PRODUIT — {op.name}"[:256],
        "description": (
            f"🖼️ **Aperçu du marché** — {len(photos)} annonces du même modèle "
            f"(vendeurs / variantes / prix différents)\n\n{description}"
        )[:3900],
        "color": color,
        "fields": fields,
        "url": ref_url or explore,
        "footer": {
            "text": (
                f"📊 Fiches produit · {develop_note} · "
                f"{volume} annonces · {op.unique_sellers} vendeurs · "
                "prix achat = article + frais"
            )[:2048]
        },
        "timestamp": _utcnow().isoformat(),
    }
    if photos:
        main["image"] = {"url": photos[0]}

    embeds: list[dict[str, Any]] = [main]
    for item in mosaic_links[1:MAX_MOSAIC_PHOTOS]:
        embeds.append(
            {
                "url": item.listing_url or explore,
                "color": color,
                "description": (item.title or "")[:256] or None,
                "image": {"url": item.photo_url},
            }
        )
    return {"embeds": embeds}


def pick_best_detector_opportunity(
    *,
    force: bool = False,
    exclude_keys: set[str] | None = None,
) -> tuple[Opportunity, NicheSnapshot] | None:
    """Meilleure niche déjà publiée par le détecteur, pas encore fichée / examinée."""
    posted_fiches = _load_posted_fiche_keys()
    skipped = {} if force else _load_skipped_fiche_keys()
    exclude = exclude_keys or set()
    validated = _validated_niche_keys()
    if not validated:
        log.info("fiche_waiting_for_detector", reason="no_posted_niches_pending_fiche")
        return None

    for niche_key, _score, _name in validated:
        if niche_key in posted_fiches and not force:
            continue
        if niche_key in skipped or niche_key in exclude:
            continue
        snap = _snapshot_for_key(niche_key)
        if snap is None:
            if not force:
                _mark_fiche_skipped(niche_key, reason="no_snapshot")
            continue
        listings_n = int(snap.listing_count or 0)
        sellers_n = int(snap.unique_sellers or 0)
        if listings_n < MIN_FICHE_LISTINGS:
            if not force:
                _mark_fiche_skipped(
                    niche_key,
                    reason=f"listings_low:{listings_n}<{MIN_FICHE_LISTINGS}",
                )
            continue
        if sellers_n < MIN_FICHE_SELLERS:
            if not force:
                _mark_fiche_skipped(
                    niche_key,
                    reason=f"sellers_low:{sellers_n}<{MIN_FICHE_SELLERS}",
                )
            continue
        # Niches déjà postées par le détecteur : score historique + ignore avoid.
        op = snapshot_to_opportunity(snap, for_fiche=True)
        if op is None or op.score < MIN_FICHE_SCORE:
            if not force:
                score_v = float(getattr(op, "score", 0.0) or 0.0) if op else 0.0
                _mark_fiche_skipped(
                    niche_key,
                    reason=f"score_low:{score_v:.1f}<{MIN_FICHE_SCORE}",
                )
            continue
        return op, snap
    log.info(
        "fiche_no_eligible_detector_niche",
        validated=len(validated),
        posted_fiches=len(posted_fiches),
        skipped=len(skipped),
    )
    return None


def select_next_fiche(
    *,
    force: bool = False,
    develop: bool = True,
    develop_seconds: float | None = None,
    fast: bool = False,
    headless: bool = True,
) -> NicheProductSheet | None:
    """Pipeline : meilleure niche détecteur → deep-dive → fiche.

    Si le build échoue après deep-dive, marque la niche et tente la suivante
    (sans re-brûler 1 h) pour garantir une publication dans le cycle.
    """
    tried: set[str] = set()
    allow_develop = develop
    developed_once = False

    while True:
        picked = pick_best_detector_opportunity(force=force, exclude_keys=tried)
        if picked is None:
            return None
        op, snap = picked
        tried.add(op.niche_key)

        # Préflight : mosaïque / score avant de brûler 1 h de deep-dive
        preflight = build_niche_product_sheet(op, snap=snap)
        mosaic_n = len(_collect_mosaic(snap))
        if preflight is None and mosaic_n >= MIN_MOSAIC_PHOTOS:
            reason = _sheet_failure_reason(op, snap)
            _mark_fiche_skipped(op.niche_key, reason=f"preflight:{reason}")
            continue

        developed_minutes = 0
        snap_final = snap
        op_candidates: list[Opportunity] = [op]
        min_score = MIN_FICHE_SCORE

        if allow_develop and not developed_once:
            target_duration = float(
                develop_seconds
                if develop_seconds is not None
                else (
                    FICHES_DEVELOP_FAST_SECONDS
                    if fast
                    else float(
                        getattr(get_settings(), "fiches_develop_seconds", None)
                        or FICHES_DEVELOP_SECONDS
                    )
                )
            )
            summary = develop_niche(
                op,
                duration_seconds=develop_seconds,
                headless=headless,
                fast=fast,
            )
            if not _develop_meets_minimum(
                summary,
                target_seconds=target_duration,
                fast=fast,
            ):
                _mark_fiche_skipped(
                    op.niche_key,
                    reason=(
                        f"develop_insufficient:rounds={summary.get('rounds')}"
                        f":elapsed={summary.get('elapsed_s')}"
                    ),
                )
                log.warning(
                    "fiche_develop_insufficient",
                    niche_key=op.niche_key,
                    rounds=summary.get("rounds"),
                    elapsed_s=summary.get("elapsed_s"),
                    target_s=int(target_duration),
                )
                continue
            developed_once = True
            developed_minutes = max(1, int(summary.get("elapsed_s", 0) // 60))
            snap_final = _snapshot_for_key(op.niche_key) or snap
            op_refresh = snapshot_to_opportunity(snap_final, for_fiche=True)
            # Conservateur : on garde l’opportunité d’origine si le refresh
            # est None / score trop bas (ex. dip après enrichissement).
            op_candidates = []
            if op_refresh is not None:
                op_candidates.append(op_refresh)
            op_candidates.append(op)
            min_score = MIN_FICHE_SCORE_AFTER_DEVELOP
            allow_develop = False  # les essais suivants sont rapides

        sheet: NicheProductSheet | None = None
        used_op: Opportunity | None = None
        for candidate in op_candidates:
            sheet = build_niche_product_sheet(
                candidate, snap=snap_final, min_score=min_score
            )
            if sheet is not None:
                used_op = candidate
                break

        if sheet is None:
            reason = _sheet_failure_reason(
                op_candidates[0] if op_candidates else op,
                snap_final,
                min_score=min_score,
            )
            _mark_fiche_skipped(
                op.niche_key,
                reason=(
                    f"after_develop:{reason}"
                    if developed_minutes > 0
                    else f"build:{reason}"
                ),
            )
            log.warning(
                "fiche_build_failed",
                niche_key=op.niche_key,
                reason=reason,
                developed_minutes=developed_minutes,
                candidates=len(op_candidates),
            )
            continue

        log.info(
            "fiche_selected",
            niche_key=sheet.opportunity.niche_key,
            score=sheet.opportunity.score,
            used_refresh=bool(
                used_op is not None and used_op is not op and developed_minutes > 0
            ),
            developed_minutes=developed_minutes,
        )
        return _with_developed_minutes(sheet, developed_minutes)


def post_next_fiche_to_discord(
    *,
    force: bool = False,
    develop: bool = True,
    develop_seconds: float | None = None,
    fast: bool = False,
    headless: bool = True,
) -> dict[str, Any]:
    """Publie au plus une fiche — DISCORD_CHANNEL_FICHES_PRODUIT uniquement."""
    settings = get_settings()
    channel = _channel_fiches(settings)
    develop_paced = develop and not fast
    if not channel or not settings.discord_bot_token.strip():
        return {"posted": 0, "reason": "channel_or_token_missing"}
    refused = _validate_fiches_post_channel(settings, channel)
    if refused:
        return {"posted": 0, "reason": refused}

    cooldown_s = int(
        fiche_cooldown_remaining_seconds(develop_paced=develop_paced)
    )
    if cooldown_s > 0 and not force:
        return {
            "posted": 0,
            "reason": "cooldown",
            "cooldown_s": cooldown_s,
        }

    if develop and not fast and develop_seconds is None:
        develop_seconds = _effective_develop_seconds(None)
    elif develop and not fast and develop_seconds is not None:
        develop_seconds = _effective_develop_seconds(develop_seconds)

    with DiscordNotifier(settings) as notifier:
        _clear_fiche_pending(notifier, channel)
        sheet = select_next_fiche(
            force=force,
            develop=develop,
            develop_seconds=develop_seconds,
            fast=fast,
            headless=headless,
        )
        if sheet is None:
            validated = _validated_niche_keys()
            posted = _load_posted_fiche_keys()
            skipped = _load_skipped_fiche_keys()
            if not validated:
                reason = "waiting_for_detector"
            elif all(k in posted or k in skipped for k, _, _ in validated):
                reason = "no_eligible_niche_left"
            else:
                reason = "no_buildable_niche"
            _clear_fiche_pending(notifier, channel)
            _maybe_post_waiting_status(notifier, channel, reason=reason)
            return {
                "posted": 0,
                "reason": reason,
                "cooldown_s": 0,
                "validated": len(validated),
                "posted_keys": len(posted),
                "skipped_keys": len(skipped),
            }

        _clear_fiche_pending(notifier, channel)
        payload = build_fiche_discord_payload(sheet)
        notifier.post_message(channel, payload)

    _mark_fiche_posted(sheet.opportunity.niche_key)
    log.info(
        "fiche_produit_posted",
        niche_key=sheet.opportunity.niche_key,
        name=sheet.opportunity.name,
        mosaic=len(sheet.mosaic),
        score=sheet.opportunity.score,
        channel_id=channel,
        buy_landed=sheet.buy_landed_eur,
        developed_minutes=sheet.developed_minutes,
    )
    log.info(
        "fiche_chain_next",
        channel_id=channel,
        gap_s=int(FICHES_INTERVAL_SECONDS),
        hint="Prochaine fiche au plus tôt dans 1 h (niche différente)",
    )
    return {
        "posted": 1,
        "niche_key": sheet.opportunity.niche_key,
        "name": sheet.opportunity.name,
        "mosaic": len(sheet.mosaic),
        "score": sheet.opportunity.score,
        "channel_id": channel,
        "buy_landed_eur": sheet.buy_landed_eur,
        "developed_minutes": sheet.developed_minutes,
    }


def run_fiches_cycle(
    *,
    force: bool = False,
    develop: bool = True,
    develop_seconds: float | None = None,
    fast: bool = False,
    headless: bool = True,
) -> dict[str, Any]:
    summary = post_next_fiche_to_discord(
        force=force,
        develop=develop,
        develop_seconds=develop_seconds,
        fast=fast,
        headless=headless,
    )
    log.info("fiches_cycle_done", **summary)
    return summary


def run_fiches_loop(
    *,
    interval_seconds: float | None = None,
    develop_seconds: float | None = None,
    fast: bool = False,
    headless: bool = True,
) -> None:
    """Boucle : niche détecteur → deep-dive ~1h → fiche (analyse seule) → recommence."""
    base = float(interval_seconds or 120.0)
    while True:
        summary: dict[str, Any] = {"posted": 0}
        try:
            summary = run_fiches_cycle(
                force=False,
                develop=True,
                develop_seconds=develop_seconds,
                fast=fast,
                headless=headless,
            )
            if summary.get("posted"):
                sleep_for = FICHES_INTERVAL_SECONDS
                log.info(
                    "fiche_loop_next_start_scheduled",
                    seconds=int(sleep_for),
                    channel_id=summary.get("channel_id"),
                )
            else:
                reason = str(summary.get("reason") or "")
                rem = fiche_cooldown_remaining_seconds(develop_paced=not fast)
                if reason == "cooldown" and rem > 0:
                    sleep_for = rem + 5.0
                elif reason in {
                    "waiting_for_detector",
                    "no_eligible_niche_left",
                    "no_buildable_niche",
                }:
                    sleep_for = base
                else:
                    sleep_for = min(base, 180.0)
                sleep_for += random.uniform(0.05, 0.15) * base
        except Exception as exc:  # noqa: BLE001
            log.exception("fiches_loop_error", error=str(exc)[:200])
            sleep_for = base
        posted = bool(summary.get("posted"))
        log.info("fiches_loop_sleep", seconds=int(sleep_for), posted=posted)
        if posted:
            time.sleep(max(5.0, sleep_for))
        else:
            time.sleep(max(60.0, sleep_for))

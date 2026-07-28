"""Pipeline permanent du détecteur de niches.

Collecte → analyse → clusters/scores → publication Discord
uniquement si opportunité intéressante (et nouvelle / forcée).

Anti-répétition + anti-ban : analyses toujours différentes, scrape prudent.
"""

from __future__ import annotations

import random
import time
from typing import Any

from vinted_bot.config import get_settings
from vinted_bot.services.market_intel import run_market_intel_cycle
from vinted_bot.services.opportunity_engine import (
    PUBLISH_MIN_SCORE,
    Opportunity,
    filter_publishable_opportunities,
    select_opportunities,
)
from vinted_bot.utils.logging import get_logger

log = get_logger(__name__)

DEFAULT_DETECTOR_INTERVAL = 360.0  # ~6 min + jitter → cycle toutes les ~7–9 min
SIGNAL_CHECKPOINT = "detector:last_signals"
RATE_LIMIT_CHECKPOINT = "detector:rate_limit_cooldown"


def analyze_opportunities(*, limit: int = 12) -> list[Opportunity]:
    """Recalcule la sélection d'opportunités après mise à jour des clusters."""
    return select_opportunities(limit=limit)


def _rate_limit_sleep_remaining() -> float:
    from vinted_bot.db.repositories import get_checkpoint
    from vinted_bot.db.session import session_scope

    with session_scope() as session:
        data = get_checkpoint(session, RATE_LIMIT_CHECKPOINT) or {}
    until = data.get("until") if isinstance(data, dict) else None
    if not until:
        return 0.0
    try:
        from datetime import datetime, timezone

        dt = datetime.fromisoformat(str(until).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())
    except ValueError:
        return 0.0


def _mark_rate_limit_cooldown(seconds: float) -> None:
    from datetime import datetime, timedelta, timezone

    from vinted_bot.db.repositories import set_checkpoint
    from vinted_bot.db.session import session_scope

    until = datetime.now(timezone.utc) + timedelta(seconds=max(30.0, seconds))
    with session_scope() as session:
        set_checkpoint(
            session,
            RATE_LIMIT_CHECKPOINT,
            {"until": until.isoformat(), "seconds": seconds},
        )
    log.warning("detector_rate_limit_cooldown", seconds=seconds)


def run_collect_cycle(
    *,
    max_items: int | None = None,
    headless: bool | None = None,
    include_brand_scrape: bool = False,
) -> dict[str, Any]:
    """Collecte permanente multi-catégories — prudent anti-ban.

    Par défaut : discovery seule (pas les 40+ marques fashion → 429).
    """
    from vinted_bot.services.niche_detector import run_discovery_collect

    settings = get_settings()
    use_headless = settings.scrape_headless if headless is None else headless

    cool = _rate_limit_sleep_remaining()
    if cool > 0:
        log.info("detector_collect_waiting_cooldown", seconds=round(cool, 1))
        time.sleep(min(cool, 180.0))

    discovery: dict[str, Any] = {}
    try:
        discovery = run_discovery_collect(
            headless=use_headless,
            max_probes=6,  # moins de requêtes / cycle
            max_items=max_items or 24,
        )
    except Exception as exc:  # noqa: BLE001
        err = str(exc)
        log.warning("detector_discovery_collect_error", error=err[:200])
        discovery = {"error": err[:200]}
        if "rate_limit" in err.lower() or "429" in err:
            _mark_rate_limit_cooldown(120.0 + random.uniform(0, 60))

    brand_summary: dict[str, Any] = {"skipped": True}
    if include_brand_scrape:
        from vinted_bot.services.scrape_search import scrape_all_configured

        # Filet marques très léger et silencieux Discord
        brand_max = 2 if max_items is None else min(max_items, 2)
        try:
            results = scrape_all_configured(
                max_items=brand_max,
                headless=use_headless,
                max_discord_posts=0,
            )
            brand_summary = {
                "searches": len(results),
                "fetched": sum(int(r.items_found or 0) for r in results),
                "upserted": sum(int(r.items_upserted or 0) for r in results),
            }
        except Exception as exc:  # noqa: BLE001
            brand_summary = {"error": str(exc)[:200]}

    summary = {
        "discovery": discovery,
        "brand_searches": brand_summary,
        "fetched": int(discovery.get("fetched") or 0)
        + int(brand_summary.get("fetched") or 0),
        "upserted": int(discovery.get("upserted") or 0)
        + int(brand_summary.get("upserted") or 0),
        "multi_category": True,
        "silent_discord_collect": True,
    }
    log.info("detector_collect_done", **summary)
    return summary


def run_detector_cycle(
    *,
    collect: bool = True,
    analyze: bool = True,
    post_discord: bool = True,
    force_discord: bool = False,
    reconcile: bool = True,
    stale_hours: float = 48.0,
    max_items: int | None = None,
    headless: bool | None = None,
) -> dict[str, Any]:
    """Un tour complet du pipeline détecteur."""
    collect_summary: dict[str, Any] = {"skipped": True}
    if collect:
        try:
            collect_summary = run_collect_cycle(
                max_items=max_items,
                headless=headless,
                include_brand_scrape=False,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("detector_collect_error", error=str(exc))
            collect_summary = {"error": str(exc)[:200]}

    intel: dict[str, Any] = {"skipped": True}
    if analyze:
        intel = run_market_intel_cycle(
            post_discord=False,
            reconcile=reconcile,
            stale_hours=stale_hours,
            force_discord=False,
        )

    opportunities = analyze_opportunities()
    publishable = filter_publishable_opportunities(opportunities)
    new_signals = _detect_new_signals(opportunities)
    if new_signals and not force_discord:
        signaled = [op for op in publishable if op.niche_key in new_signals]
        if signaled:
            publishable = signaled + [
                op for op in publishable if op.niche_key not in new_signals
            ]

    posted = 0
    if post_discord:
        from vinted_bot.services.market_intel import post_interesting_niches_to_discord

        # Radar 🧠 uniquement dans DISCORD_CHANNEL_NICHES.
        # Les fiches produit vont uniquement via `vinted-bot fiches-produit`
        # → DISCORD_CHANNEL_FICHES_PRODUIT (jamais ce salon).
        posted = post_interesting_niches_to_discord(
            opportunities=publishable,
            force=force_discord,
            prefer_keys=new_signals,
        )

    _store_signal_checkpoint(opportunities)

    summary = {
        "collect": collect_summary,
        "snapshots": intel.get("snapshots", 0),
        "scored": intel.get("scored", 0),
        "reconciled": intel.get("reconciled", 0),
        "opportunities": len(opportunities),
        "publishable": len(publishable),
        "new_signals": len(new_signals),
        "discord_posted": posted,
        "publish_min_score": PUBLISH_MIN_SCORE,
    }
    log.info("detector_cycle_done", **summary)
    return summary


def _signal_fingerprint(op: Opportunity) -> str:
    return f"{op.niche_key}|{op.score:.0f}|{'+'.join(op.signals[:3])}"


def _detect_new_signals(ops: list[Opportunity]) -> set[str]:
    """Compare les signaux courants à l'historique checkpoint (nouveaux changements)."""
    from vinted_bot.db.repositories import get_checkpoint
    from vinted_bot.db.session import session_scope

    with session_scope() as session:
        data = get_checkpoint(session, SIGNAL_CHECKPOINT) or {}
    prev = data.get("fps") if isinstance(data, dict) else None
    prev_set = set(prev) if isinstance(prev, list) else set()
    new_keys: set[str] = set()
    for op in ops:
        if not op.signals:
            continue
        fp = _signal_fingerprint(op)
        if fp not in prev_set:
            new_keys.add(op.niche_key)
    return new_keys


def _store_signal_checkpoint(ops: list[Opportunity]) -> None:
    from vinted_bot.db.repositories import set_checkpoint
    from vinted_bot.db.session import session_scope

    fps = [_signal_fingerprint(op) for op in ops if op.signals][:80]
    with session_scope() as session:
        set_checkpoint(session, SIGNAL_CHECKPOINT, {"fps": fps})


def run_detector_loop(
    *,
    interval_seconds: float | None = None,
    collect: bool = True,
    post_discord: bool = True,
    max_items: int | None = None,
    headless: bool | None = None,
) -> None:
    """Boucle permanente sans interruption — analyses toujours différentes."""
    interval = (
        interval_seconds
        if interval_seconds is not None
        else DEFAULT_DETECTOR_INTERVAL
    )
    log.info(
        "detector_loop_start",
        interval_seconds=interval,
        collect=collect,
        post_discord=post_discord,
        publish_min_score=PUBLISH_MIN_SCORE,
        anti_repeat=True,
        anti_ban=True,
    )
    while True:
        try:
            cool = _rate_limit_sleep_remaining()
            if cool > 5:
                log.info("detector_loop_cooldown", seconds=round(cool, 1))
                time.sleep(cool)

            summary = run_detector_cycle(
                collect=collect,
                analyze=True,
                post_discord=post_discord,
                max_items=max_items,
                headless=headless,
            )
            # Si rate-limit détecté pendant collect → allonger la pause
            collect_blob = str(summary.get("collect") or "")
            if "rate_limit" in collect_blob.lower() or "429" in collect_blob:
                _mark_rate_limit_cooldown(90.0 + random.uniform(30, 90))
        except Exception as exc:  # noqa: BLE001
            log.exception("detector_cycle_error", error=str(exc))
            # Ne jamais s'arrêter — pause puis reprise
            time.sleep(60.0 + random.uniform(0, 30))

        # Jitter entre cycles (empreinte moins robotique)
        jitter = random.uniform(0.15, 0.45) * interval
        sleep_for = max(90.0, interval + jitter)
        log.info("detector_loop_sleep", seconds=round(sleep_for, 1))
        time.sleep(sleep_for)

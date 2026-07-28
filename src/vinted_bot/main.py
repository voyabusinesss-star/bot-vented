"""Point d'entrée CLI du bot."""

from __future__ import annotations

import argparse

from vinted_bot.config import get_settings
from vinted_bot.utils.logging import get_logger, setup_logging


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vinted-bot",
        description="Bot de scraping Vinted",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="vinted-bot 0.1.0",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("hello", help="Vérifie que le projet démarre correctement")
    sub.add_parser("db-check", help="Vérifie la connexion PostgreSQL")
    sub.add_parser(
        "db-seed",
        help="Insert/upsert une annonce de test (vérifie dedup vinted_id)",
    )

    scrape = sub.add_parser(
        "scrape",
        help="Scrape une recherche Vinted et enregistre les annonces",
    )
    scrape.add_argument(
        "--query",
        "-q",
        help='Texte de recherche (ex: "nike"). Ignoré si --all.',
    )
    scrape.add_argument(
        "--all",
        action="store_true",
        help="Scrape toutes les recherches de config/searches.yaml",
    )
    scrape.add_argument(
        "--loop",
        action="store_true",
        help="Tourne en continu 24/7 (cycles automatiques)",
    )
    scrape.add_argument(
        "--interval",
        type=float,
        default=None,
        help="Secondes entre deux cycles --loop (défaut: searches.yaml)",
    )
    scrape.add_argument(
        "--once",
        action="store_true",
        help="Exécute un seul passage",
    )
    scrape.add_argument(
        "--max-items",
        type=int,
        default=None,
        help="Nombre max d'annonces par recherche (défaut: searches.yaml)",
    )
    scrape.add_argument(
        "--headed",
        action="store_true",
        help="Ouvre le navigateur en mode visible (debug)",
    )

    sub.add_parser(
        "discord-test",
        help="Envoie un embed de test dans le salon regroupement (DISCORD_CHANNEL_ALL)",
    )

    sub.add_parser(
        "discord-interactions",
        help="Écoute les interactions Discord des filtres et alertes",
    )
    sub.add_parser(
        "post-mes-alertes",
        help="Poste le panneau Mes alertes dans DISCORD_CHANNEL_MES_ALERTES",
    )
    sub.add_parser(
        "post-detector-apercu",
        help="Poste l'aperçu détecteur dans DISCORD_CHANNEL_NICHES_DEMO",
    )
    intro_parser = sub.add_parser(
        "post-niches-vinted-intro",
        help="Poste l'intro niches vinted (embed + fichier Excel 1000 niches)",
    )
    intro_parser.add_argument(
        "--catalog-path",
        default=None,
        help="Chemin vers Resello_1000_Niches_Vinted.xlsx (défaut: config/)",
    )

    niche = sub.add_parser(
        "niche-detect",
        help="Détecteur de niches (sondes + market-intel)",
    )
    niche.add_argument(
        "--loop",
        action="store_true",
        help="Tourne en continu (cycles niches)",
    )
    niche.add_argument(
        "--once",
        action="store_true",
        help="Un seul cycle puis stop",
    )
    niche.add_argument(
        "--interval",
        type=float,
        default=None,
        help="Secondes entre cycles --loop (défaut: config/niches.yaml)",
    )
    niche.add_argument(
        "--headed",
        action="store_true",
        help="Navigateur visible (debug)",
    )
    niche.add_argument(
        "--no-discord",
        action="store_true",
        help="Analyse sans poster sur Discord",
    )

    market = sub.add_parser(
        "market-intel",
        help="Moteur d'intelligence de marché (agrégats, score, tops Discord)",
    )
    market.add_argument(
        "--loop",
        action="store_true",
        help="Tourne en continu",
    )
    market.add_argument(
        "--once",
        action="store_true",
        help="Un seul cycle puis stop",
    )
    market.add_argument(
        "--interval",
        type=float,
        default=None,
        help="Secondes entre cycles --loop (défaut: 900)",
    )
    market.add_argument(
        "--no-discord",
        action="store_true",
        help="Calcule sans poster sur Discord",
    )
    market.add_argument(
        "--force-discord",
        action="store_true",
        help="Ignore les cooldowns Discord (classements + rapport tendances du jour)",
    )
    market.add_argument(
        "--no-reconcile",
        action="store_true",
        help="Ne marque pas les annonces stale comme disparues",
    )
    market.add_argument(
        "--stale-hours",
        type=float,
        default=48.0,
        help="Heures sans observation avant disparition (défaut: 48)",
    )

    detector = sub.add_parser(
        "detector",
        help=(
            "Pipeline permanent niches : collecte → analyse → scores → "
            "Discord seulement si opportunité intéressante"
        ),
    )
    detector.add_argument(
        "--loop",
        action="store_true",
        help="Tourne en continu (recommandé)",
    )
    detector.add_argument(
        "--once",
        action="store_true",
        help="Un seul cycle puis stop",
    )
    detector.add_argument(
        "--interval",
        type=float,
        default=None,
        help="Secondes entre cycles --loop (défaut: 360 ≈ 6 min + jitter)",
    )
    detector.add_argument(
        "--no-collect",
        action="store_true",
        help="Skip scrape (analyse seule — utile si scrape --loop ailleurs)",
    )
    detector.add_argument(
        "--no-discord",
        action="store_true",
        help="Analyse sans poster sur Discord",
    )
    detector.add_argument(
        "--force-discord",
        action="store_true",
        help="Publie les opportunités intéressantes même déjà vues",
    )
    detector.add_argument(
        "--max-items",
        type=int,
        default=None,
        help="Max annonces par recherche pendant la collecte",
    )
    detector.add_argument(
        "--headed",
        action="store_true",
        help="Navigateur visible pendant la collecte",
    )

    fiches = sub.add_parser(
        "fiches-produit",
        help=(
            "Fiches produit premium : réanalyse des niches déjà validées "
            "par le détecteur (1 fiche / heure max)"
        ),
    )
    fiches.add_argument(
        "--loop",
        action="store_true",
        help="Tourne en continu (1 fiche / heure max)",
    )
    fiches.add_argument(
        "--once",
        action="store_true",
        help="Tente une fiche puis stop",
    )
    fiches.add_argument(
        "--force",
        action="store_true",
        help="Ignore le cooldown 1h (debug)",
    )
    fiches.add_argument(
        "--fast",
        action="store_true",
        help="Deep-dive court (~2 min) au lieu de 1h — debug seulement",
    )
    fiches.add_argument(
        "--no-develop",
        action="store_true",
        help="Skip deep-dive (interdit en --loop ; debug --once seulement)",
    )
    fiches.add_argument(
        "--develop-seconds",
        type=float,
        default=None,
        help="Durée deep-dive (défaut: 3600 = 1h)",
    )
    fiches.add_argument(
        "--interval",
        type=float,
        default=None,
        help="Secondes entre polls si aucune niche détecteur (défaut: 300)",
    )
    fiches.add_argument(
        "--headed",
        action="store_true",
        help="Navigateur visible pendant le deep-dive",
    )

    return parser


def cmd_hello(log) -> None:
    settings = get_settings()
    log.info(
        "bot_ready",
        app_env=settings.app_env,
        log_level=settings.log_level,
    )
    print(f"OK — env={settings.app_env} python-ready")


def cmd_db_check(log) -> None:
    from vinted_bot.db.session import check_connection

    check_connection()
    log.info("db_ok", database_url=get_settings().database_url.split("@")[-1])
    print("OK — connexion PostgreSQL réussie")


def cmd_db_seed(log) -> None:
    from vinted_bot.db.repositories import get_listing_by_vinted_id, upsert_listing
    from vinted_bot.db.session import session_scope

    with session_scope() as session:
        listing, _ = upsert_listing(
            session,
            vinted_id=999001,
            title="Annonce test Phase 1",
            url="https://www.vinted.fr/items/999001",
            price_cents=1999,
            brand="Nike",
            size="M",
            condition="Très bon état",
            photo_urls=[
                "https://example.com/photo1.jpg",
                "https://example.com/photo2.jpg",
            ],
            raw_json={"source": "db-seed"},
        )
        again, _ = upsert_listing(
            session,
            vinted_id=999001,
            title="Annonce test Phase 1 (mise à jour)",
            url="https://www.vinted.fr/items/999001",
            price_cents=1499,
            brand="Nike",
            size="M",
            condition="Très bon état",
            photo_urls=["https://example.com/photo1.jpg"],
            raw_json={"source": "db-seed", "updated": True},
        )
        found = get_listing_by_vinted_id(session, 999001)
        assert found is not None
        log.info(
            "db_seed_ok",
            listing_id=listing.id,
            updated_id=again.id,
            price_cents=found.price_cents,
            photos=len(found.photos),
        )
        print(
            f"OK — upsert listing id={found.id} "
            f"price_cents={found.price_cents} photos={len(found.photos)}"
        )


def cmd_scrape(args, log) -> None:
    settings = get_settings()
    headless = not args.headed and settings.scrape_headless

    if args.loop:
        from vinted_bot.jobs.scheduler import run_scrape_loop

        print(
            "Démarrage boucle 24/7 — Ctrl+C pour arrêter. "
            "Logs: loop_cycle_start / loop_cycle_done"
        )
        try:
            run_scrape_loop(
                max_items=args.max_items,
                headless=headless,
                interval_seconds=args.interval,
            )
        except KeyboardInterrupt:
            print("\nBoucle arrêtée.")
        return

    if not args.once and not args.all:
        print(
            "Utilise:\n"
            "  scrape --all --once\n"
            "  scrape --query nike --once\n"
            "  scrape --loop"
        )
        return

    if args.all:
        from vinted_bot.services.scrape_search import scrape_all_configured

        results = scrape_all_configured(
            max_items=args.max_items,
            headless=headless,
        )
        if not results:
            print("Aucune recherche active (vérifie config/searches.yaml + IDs Discord).")
            return
        total_created = sum(r.items_created for r in results)
        total_posted = sum(r.items_posted_discord for r in results)
        bootstraps = sum(1 for r in results if r.bootstrap)
        print(
            f"OK — searches={len(results)} created={total_created} "
            f"discord={total_posted} bootstraps={bootstraps}"
        )
        for result in results:
            flag = "bootstrap" if result.bootstrap else "live"
            print(
                f"  - [{flag}] {result.query!r} "
                f"found={result.items_found} created={result.items_created} "
                f"discord={result.items_posted_discord}"
            )
        return

    if not args.query:
        print("Indique --query \"marque\" ou utilise --all / --loop")
        return

    from vinted_bot.config_loader import load_searches_config
    from vinted_bot.services.scrape_search import scrape_search_once

    max_items = args.max_items or load_searches_config().max_items
    result = scrape_search_once(
        args.query,
        max_items=max_items,
        headless=headless,
    )
    log.info(
        "scrape_cli_done",
        query=result.query,
        found=result.items_found,
        created=result.items_created,
        posted_discord=result.items_posted_discord,
        bootstrap=result.bootstrap,
        run_id=result.scrape_run_id,
    )
    mode = "bootstrap (pas de Discord)" if result.bootstrap else "live"
    print(
        f"OK — query={result.query!r} mode={mode} "
        f"found={result.items_found} created={result.items_created} "
        f"discord={result.items_posted_discord} "
        f"run_id={result.scrape_run_id}"
    )
    for item in result.items[:5]:
        price = (
            f"{item.price_cents / 100:.2f} {item.currency}"
            if item.price_cents is not None
            else "?"
        )
        print(f"  - [{item.vinted_id}] {item.title} — {price}")
    if result.items_found > 5:
        print(f"  ... +{result.items_found - 5} autres")


def cmd_discord_test(log) -> None:
    from vinted_bot.notify.discord import DiscordNotifier

    settings = get_settings()
    if not settings.discord_ready():
        print(
            "Discord non configuré. Remplis DISCORD_BOT_TOKEN, "
            "DISCORD_CHANNEL_ALL et au moins un canal marque "
            "(ex. DISCORD_CHANNEL_NIKE) dans .env (voir README)."
        )
        return

    with DiscordNotifier(settings) as notifier:
        notifier.post_test_message()
    log.info("discord_test_ok")
    print("OK — message de test envoyé dans le salon regroupement (#all)")


def cmd_post_mes_alertes(log) -> None:
    from vinted_bot.interactions.alerts_panel import build_mes_alertes_panel_payload
    from vinted_bot.interactions.discord_api import DiscordInteractionClient

    settings = get_settings()
    if not settings.discord_bot_token.strip():
        print("DISCORD_BOT_TOKEN manquant dans .env")
        return

    with DiscordInteractionClient(settings) as client:
        channel_id = client.mes_alertes_channel_id()
        if not channel_id:
            print(
                "DISCORD_CHANNEL_MES_ALERTES manquant dans .env "
                "(ID du salon mes alertes)."
            )
            return
        payload = build_mes_alertes_panel_payload()
        message = client.post_channel_payload(channel_id, payload)
        log.info(
            "mes_alertes_panel_posted",
            channel_id=channel_id,
            message_id=message.get("id"),
        )
        print(f"OK — panneau Mes alertes posté dans le salon {channel_id}")


def cmd_post_detector_apercu(log) -> None:
    from vinted_bot.interactions.detector_preview_panel import (
        build_detector_preview_panel_payload,
    )
    from vinted_bot.interactions.discord_api import (
        DiscordInteractionClient,
        sanitize_guild_id,
    )

    settings = get_settings()
    if not settings.discord_bot_token.strip():
        print("DISCORD_BOT_TOKEN manquant dans .env")
        return

    with DiscordInteractionClient(settings) as client:
        channel_id = client.niches_demo_channel_id()
        if not channel_id:
            print(
                "DISCORD_CHANNEL_NICHES_DEMO manquant dans .env "
                "(ID du salon demo / aperçu détecteur de niches)."
            )
            return
        payload = build_detector_preview_panel_payload()
        guild_id = sanitize_guild_id(getattr(settings, "discord_guild_id", "") or "")
        webhook_url = getattr(settings, "discord_webhook_niches_demo", "") or ""
        if guild_id:
            message = client.post_channel_payload_as_guild(
                channel_id,
                payload,
                guild_id=guild_id,
                webhook_url=webhook_url,
            )
        else:
            message = client.post_channel_payload(channel_id, payload)
        log.info(
            "detector_preview_panel_posted",
            channel_id=channel_id,
            message_id=message.get("id"),
        )
        print(f"OK — aperçu détecteur posté dans le salon {channel_id}")


def cmd_post_niches_vinted_intro(args, log) -> None:
    from vinted_bot.interactions.discord_api import (
        DiscordInteractionClient,
        sanitize_guild_id,
    )
    from vinted_bot.interactions.niches_vinted_intro_panel import (
        build_niches_vinted_intro_payload,
        load_niches_vinted_catalog_bytes,
        post_niches_vinted_intro_message,
    )

    settings = get_settings()
    if not settings.discord_bot_token.strip():
        print("DISCORD_BOT_TOKEN manquant dans .env")
        return

    catalog_path = getattr(args, "catalog_path", None)
    try:
        catalog_bytes, catalog_name = load_niches_vinted_catalog_bytes(catalog_path)
    except (FileNotFoundError, ValueError) as exc:
        print(str(exc))
        return

    with DiscordInteractionClient(settings) as client:
        channel_id = client.niches_vinted_channel_id()
        if not channel_id:
            print(
                "DISCORD_CHANNEL_NICHES_VINTED manquant dans .env "
                "(ID du salon niches vinted)."
            )
            return
        guild_id = sanitize_guild_id(getattr(settings, "discord_guild_id", "") or "")
        webhook_url = getattr(settings, "discord_webhook_niches_vinted", "") or ""
        if guild_id and webhook_url.strip():
            message = post_niches_vinted_intro_message(
                client,
                channel_id=channel_id,
                guild_id=guild_id,
                webhook_url=webhook_url,
                catalog_bytes=catalog_bytes,
                catalog_filename=catalog_name,
            )
        elif guild_id:
            print(
                "⚠️  DISCORD_WEBHOOK_NICHES_VINTED vide — le message s’affichera "
                "avec l’avatar du bot.\n"
                "   Salon → Paramètres → Intégrations → Webhooks → Nouveau → "
                "copie l’URL dans .env puis relance."
            )
            from vinted_bot.interactions.niches_vinted_intro_panel import (
                _purge_intro_channel_catalog_attachments,
                _resolve_catalog_host_channel,
                ensure_catalog_download_url,
            )

            host_channel_id = _resolve_catalog_host_channel(channel_id)
            _purge_intro_channel_catalog_attachments(client, channel_id)
            payload = build_niches_vinted_intro_payload(
                catalog_filename=catalog_name,
                download_url=ensure_catalog_download_url(
                    client,
                    host_channel_id,
                    catalog_bytes=catalog_bytes,
                    catalog_filename=catalog_name,
                ),
            )
            message = client.post_channel_payload_as_guild_with_attachments(
                channel_id,
                payload,
                guild_id=guild_id,
                webhook_url=webhook_url,
                attachments=None,
            )
        else:
            from vinted_bot.interactions.niches_vinted_intro_panel import (
                _purge_intro_channel_catalog_attachments,
                _resolve_catalog_host_channel,
                ensure_catalog_download_url,
            )

            host_channel_id = _resolve_catalog_host_channel(channel_id)
            _purge_intro_channel_catalog_attachments(client, channel_id)
            payload = build_niches_vinted_intro_payload(
                catalog_filename=catalog_name,
                download_url=ensure_catalog_download_url(
                    client,
                    host_channel_id,
                    catalog_bytes=catalog_bytes,
                    catalog_filename=catalog_name,
                ),
            )
            message = client.post_channel_payload(channel_id, payload)
        log.info(
            "niches_vinted_intro_posted",
            channel_id=channel_id,
            message_id=message.get("id"),
            catalog=catalog_name,
        )
        print(
            f"OK — intro niches vinted postée dans le salon {channel_id} "
            f"(téléchargement intégré via bouton)"
        )


def cmd_discord_interactions(log) -> None:
    from vinted_bot.interactions.gateway import run_discord_interactions

    settings = get_settings()
    if not settings.discord_bot_token.strip():
        print("DISCORD_BOT_TOKEN manquant dans .env")
        return

    print(
        "Écoute Discord (filtres + alertes) — Ctrl+C pour arrêter.\n"
        "Lance en parallèle : uv run vinted-bot scrape --loop"
    )
    try:
        run_discord_interactions()
    except KeyboardInterrupt:
        print("\nInteractions arrêtées.")


def cmd_niche_detect(args, log) -> None:
    from vinted_bot.services.niche_detector import run_niche_cycle, run_niche_loop

    headless = not bool(args.headed)
    if args.loop and not args.once:
        print(
            "Détecteur de niches — Ctrl+C pour arrêter.\n"
            "Configure DISCORD_CHANNEL_NICHES dans .env + config/niches.yaml"
        )
        try:
            run_niche_loop(interval_seconds=args.interval, headless=headless)
        except KeyboardInterrupt:
            print("\nDétecteur de niches arrêté.")
        return

    ops = run_niche_cycle(
        headless=headless,
        post_discord=not bool(args.no_discord),
    )
    log.info("niche_detect_once_done", opportunities=len(ops))
    print(f"OK — {len(ops)} niche(s) trouvée(s)")
    for op in ops[:10]:
        print(
            f"  - {op.brand} / {op.category_label}: "
            f"achat ~{op.cheap_price_eur:.0f}€ → médiane ~{op.median_price_eur:.0f}€ "
            f"(+{op.margin_pct:.0f}%)"
        )
    if len(ops) > 10:
        print(f"  ... +{len(ops) - 10} autres")


def cmd_market_intel(args, log) -> None:
    from vinted_bot.services.market_intel import (
        run_market_intel_cycle,
        run_market_intel_loop,
        top_snapshots,
    )

    if args.loop and not args.once:
        print(
            "Market intel — Ctrl+C pour arrêter.\n"
            "Salon cœur: DISCORD_CHANNEL_NICHES (études de niches).\n"
            "Salons secondaires optionnels si créés (sinon laisser vides).\n"
            "Lance scrape --loop en parallèle pour alimenter les données"
        )
        try:
            run_market_intel_loop(
                interval_seconds=args.interval,
                post_discord=not bool(args.no_discord),
            )
        except KeyboardInterrupt:
            print("\nMarket intel arrêté.")
        return

    summary = run_market_intel_cycle(
        post_discord=not bool(args.no_discord),
        reconcile=not bool(args.no_reconcile),
        stale_hours=float(args.stale_hours),
        force_discord=bool(args.force_discord),
    )
    log.info("market_intel_once_done", **summary)
    print(
        f"OK — snapshots={summary['snapshots']} scored={summary['scored']} "
        f"trends={summary.get('trends_saved', 0)} "
        f"reconciled={summary['reconciled']} "
        f"discord={summary['discord_posted']}"
    )
    # Affiche ce que le détecteur de niches publie vraiment (pas le top marques)
    from vinted_bot.services.opportunity_engine import (
        PUBLISH_MIN_SCORE,
        filter_publishable_opportunities,
        select_opportunities,
    )

    ops = select_opportunities(limit=8)
    publishable = filter_publishable_opportunities(ops)
    publishable_keys = {o.niche_key for o in publishable}
    print(f"Détecteur niches (études, publish ≥{PUBLISH_MIN_SCORE:.0f}):")
    for op in ops:
        flag = "✓" if op.niche_key in publishable_keys else "·"
        print(
            f"  {flag} {op.name}: {op.score:.0f}/100 · {op.niche_type} · {op.why_short}"
        )


def cmd_detector(args, log) -> None:
    from vinted_bot.services.detector_pipeline import (
        run_detector_cycle,
        run_detector_loop,
    )
    from vinted_bot.services.opportunity_engine import PUBLISH_MIN_SCORE

    headless = not bool(args.headed)
    if args.loop and not args.once:
        print(
            "Détecteur permanent — Ctrl+C pour arrêter.\n"
            "Flux: collecte → analyse → clusters/scores → "
            f"Discord si score ≥ {PUBLISH_MIN_SCORE:.0f}.\n"
            "Salon: DISCORD_CHANNEL_NICHES"
        )
        try:
            run_detector_loop(
                interval_seconds=args.interval,
                collect=not bool(args.no_collect),
                post_discord=not bool(args.no_discord),
                max_items=args.max_items,
                headless=headless,
            )
        except KeyboardInterrupt:
            print("\nDétecteur arrêté.")
        return

    summary = run_detector_cycle(
        collect=not bool(args.no_collect),
        analyze=True,
        post_discord=not bool(args.no_discord),
        force_discord=bool(args.force_discord),
        max_items=args.max_items,
        headless=headless,
    )
    log.info("detector_once_done", **summary)
    print(
        f"OK — collect={summary.get('collect')} "
        f"snapshots={summary.get('snapshots')} scored={summary.get('scored')} "
        f"opportunities={summary.get('opportunities')} "
        f"publishable={summary.get('publishable')} "
        f"discord={summary.get('discord_posted')}"
    )


def cmd_fiches_produit(args, log) -> None:
    from vinted_bot.services.niche_product_sheets import (
        FICHES_DEVELOP_SECONDS,
        run_fiches_cycle,
        run_fiches_loop,
    )

    headless = not bool(args.headed)
    develop = not bool(args.no_develop)
    develop_s = args.develop_seconds
    fast = bool(args.fast)

    if args.loop and not args.once:
        if not develop or fast:
            print(
                "En --loop, le deep-dive complet est obligatoire "
                "(retire --no-develop et --fast)."
            )
            return
        print(
            "Fiches produit niches — Ctrl+C pour arrêter.\n"
            "1) Niches POSTÉES par 🧠 Détecteur\n"
            "2) Meilleure niche éligible\n"
            f"3) Deep-dive ~{int((develop_s or FICHES_DEVELOP_SECONDS) // 60)} min (scrapes réels)\n"
            "4) Poste l'analyse dans DISCORD_CHANNEL_FICHES_PRODUIT (sans intro marketing)"
        )
        try:
            run_fiches_loop(
                interval_seconds=args.interval,
                develop_seconds=develop_s,
                fast=fast,
                headless=headless,
            )
        except KeyboardInterrupt:
            print("\nFiches produit arrêtées.")
        return

    summary = run_fiches_cycle(
        force=bool(args.force),
        develop=develop,
        develop_seconds=develop_s,
        fast=fast,
        headless=headless,
    )
    log.info("fiches_once_done", **summary)
    if summary.get("posted"):
        print(
            f"OK — fiche postée : {summary.get('name')} "
            f"(score={summary.get('score'):.0f}, mosaïque={summary.get('mosaic')}, "
            f"achat TTC≈{summary.get('buy_landed_eur')} €, "
            f"deep-dive={summary.get('developed_minutes')} min)"
        )
    else:
        print(
            f"Aucune fiche — reason={summary.get('reason')} "
            f"cooldown_s={summary.get('cooldown_s', 0)}\n"
            "Lance d’abord : uv run vinted-bot detector --loop"
        )


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    settings = get_settings()
    setup_logging(settings.log_level)
    log = get_logger(__name__)

    if args.command == "hello":
        cmd_hello(log)
        return
    if args.command == "db-check":
        cmd_db_check(log)
        return
    if args.command == "db-seed":
        cmd_db_seed(log)
        return
    if args.command == "scrape":
        cmd_scrape(args, log)
        return
    if args.command == "discord-test":
        cmd_discord_test(log)
        return
    if args.command == "discord-interactions":
        cmd_discord_interactions(log)
        return
    if args.command == "post-mes-alertes":
        cmd_post_mes_alertes(log)
        return
    if args.command == "post-detector-apercu":
        cmd_post_detector_apercu(log)
        return
    if args.command == "post-niches-vinted-intro":
        cmd_post_niches_vinted_intro(args, log)
        return
    if args.command == "niche-detect":
        cmd_niche_detect(args, log)
        return
    if args.command == "market-intel":
        cmd_market_intel(args, log)
        return
    if args.command == "detector":
        cmd_detector(args, log)
        return
    if args.command == "fiches-produit":
        cmd_fiches_produit(args, log)
        return

    parser.print_help()


if __name__ == "__main__":
    main()

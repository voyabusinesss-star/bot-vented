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
    reglement_parser = sub.add_parser(
        "post-reglement",
        help="Bouton validation sur le message règlement (attach ou replace)",
    )
    reglement_parser.add_argument(
        "--attach",
        action="store_true",
        help="Ajoute le bouton sur DISCORD_REGLEMENT_MESSAGE_ID (message Resello)",
    )
    reglement_parser.add_argument(
        "--replace",
        action="store_true",
        help="Supprime les anciens messages et reposte un seul règlement + bouton",
    )
    sub.add_parser(
        "setup-reglement-gates",
        help="Verrouille les salons (sauf bienvenue/règlement) jusqu'à validation",
    )
    sync_proplus = sub.add_parser(
        "sync-proplus-perms",
        help="Copie les permissions salons/catégories Resello Pro → Resello Pro+",
    )
    sync_proplus.add_argument(
        "--dry-run",
        action="store_true",
        help="Liste les salons concernés sans modifier Discord",
    )
    sync_starter = sub.add_parser(
        "sync-starter-perms",
        help="Copie les permissions Resello Pro → Resello Starter (hors outils privés)",
    )
    sync_starter.add_argument(
        "--dry-run",
        action="store_true",
        help="Liste les salons concernés sans modifier Discord",
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
    vintify_parser = sub.add_parser(
        "post-vintify-intro",
        help="Poste l'intro Vintify dans DISCORD_CHANNEL_VINTIFY (embed + aperçu + lien)",
    )
    vintify_parser.add_argument(
        "--preview-path",
        default=None,
        help="Chemin vers l'image d'aperçu Vintify (défaut: config/vintify-preview.png)",
    )
    sub.add_parser(
        "post-subscriptions",
        help="Poste les offres Starter/Pro/Pro+ dans DISCORD_CHANNEL_SUBSCRIPTIONS",
    )
    sub.add_parser(
        "post-fiscalite",
        help="Poste le panneau Guide fiscalité + PDF dans DISCORD_CHANNEL_FISCALITE",
    )
    sub.add_parser(
        "post-recruitment",
        help="Poste le panneau tickets recrutement dans DISCORD_CHANNEL_RECRUITMENT",
    )
    sub.add_parser(
        "post-support",
        help="Poste le panneau tickets aide dans DISCORD_CHANNEL_SUPPORT",
    )
    sub.add_parser(
        "post-fournisseurs",
        help="Poste le panneau Fleek dans DISCORD_CHANNEL_FOURNISSEURS",
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


def cmd_post_reglement(args, log) -> None:
    from vinted_bot.config import sanitize_discord_channel_id
    from vinted_bot.interactions.discord_api import DiscordInteractionClient
    from vinted_bot.interactions.reglement_panel import (
        attach_reglement_button,
        replace_reglement_panel,
    )

    settings = get_settings()
    if not settings.discord_bot_token.strip():
        print("DISCORD_BOT_TOKEN manquant dans .env")
        return

    with DiscordInteractionClient(settings) as client:
        channel_id = client.reglement_channel_id()
        if not channel_id:
            print(
                "DISCORD_CHANNEL_REGLEMENT manquant dans .env "
                "(ID du salon règlement)."
            )
            return

        message_id = sanitize_discord_channel_id(
            getattr(settings, "discord_reglement_message_id", "") or ""
        )
        do_attach = bool(getattr(args, "attach", False))

        if do_attach:
            if not message_id:
                print(
                    "DISCORD_REGLEMENT_MESSAGE_ID manquant — "
                    "ou utilise post-reglement (sans --attach)."
                )
                return
            try:
                message = attach_reglement_button(
                    client,
                    channel_id=channel_id,
                    message_id=message_id,
                )
            except PermissionError as exc:
                print(str(exc))
                print("Relance avec : uv run vinted-bot post-reglement --replace")
                return
            log.info(
                "reglement_button_attached",
                channel_id=channel_id,
                message_id=message.get("id"),
            )
            print(
                f"OK — bouton ajouté sur le message {message_id} "
                f"dans le salon {channel_id}"
            )
            return

        remove_ids = [message_id] if message_id else []
        remove_ids.append("1531942545410756632")
        if not message_id:
            remove_ids.append("1530629737448472617")
        message = replace_reglement_panel(
            client,
            channel_id=channel_id,
            remove_message_ids=remove_ids,
        )
        log.info(
            "reglement_panel_replaced",
            channel_id=channel_id,
            message_id=message.get("id"),
        )
        print(
            f"OK — règlement unique avec bouton dans le salon {channel_id} "
            f"(message {message.get('id')})"
        )


def cmd_setup_reglement_gates(log) -> None:
    from vinted_bot.interactions.discord_api import (
        DiscordInteractionClient,
        sanitize_guild_id,
    )
    from vinted_bot.services.reglement_gates import (
        apply_reglement_gates,
        ensure_membre_role,
        resolve_membre_preview_channel_ids,
        resolve_public_channel_ids,
    )

    settings = get_settings()
    if not settings.discord_bot_token.strip():
        print("DISCORD_BOT_TOKEN manquant dans .env")
        return

    guild_id = sanitize_guild_id(getattr(settings, "discord_guild_id", "") or "")
    if not guild_id:
        print("DISCORD_GUILD_ID manquant dans .env")
        return

    public_ids = resolve_public_channel_ids(settings)
    if not public_ids:
        print(
            "Configure DISCORD_CHANNEL_BIENVENUE et DISCORD_CHANNEL_REGLEMENT "
            "dans .env."
        )
        return

    with DiscordInteractionClient(settings) as client:
        role_id = ensure_membre_role(client, guild_id)
        preview_ids = resolve_membre_preview_channel_ids(settings)
        stats = apply_reglement_gates(
            client,
            guild_id=guild_id,
            member_role_id=role_id,
            public_channel_ids=public_ids,
            preview_channel_ids=preview_ids,
        )
        log.info("reglement_gates_setup", role_id=role_id, **stats)
        print(
            f"OK — verrouillage appliqué (rôle Membre {role_id})\n"
            f"  Salons publics (@everyone) : {stats['public']}\n"
            f"  Aperçu Membre (règlement) : {stats['gated']}\n"
            f"  Réservés abo (Starter+) : {stats.get('denied', 0)}\n"
            f"  Mis à jour : {stats['updated']} · Échecs : {stats.get('failed', 0)}"
        )


def cmd_sync_proplus_perms(args, log) -> None:
    from vinted_bot.services.discord_role_perms import sync_proplus_perms_from_pro

    settings = get_settings()
    if not settings.discord_bot_token.strip():
        print("DISCORD_BOT_TOKEN manquant dans .env")
        return
    pro = (settings.discord_role_sub_pro or "").strip()
    proplus = (settings.discord_role_sub_proplus or "").strip()
    if not pro or not proplus:
        print(
            "DISCORD_ROLE_SUB_PRO et DISCORD_ROLE_SUB_PROPLUS requis dans .env"
        )
        return
    dry = bool(getattr(args, "dry_run", False))
    print(
        f"{'[dry-run] ' if dry else ''}Copie perms Pro ({pro}) → Pro+ ({proplus})…"
    )
    try:
        stats = sync_proplus_perms_from_pro(dry_run=dry)
    except Exception as exc:  # noqa: BLE001
        print(f"Erreur : {exc}")
        return
    log.info("sync_proplus_perms_done", **stats)
    print(
        f"OK — scannés {stats['channels_scanned']} · "
        f"copiés {stats['copied']} · déjà OK {stats['already_ok']} · "
        f"sans Pro {stats['skipped_no_source']} · erreurs {stats['errors']}"
    )


def cmd_sync_starter_perms(args, log) -> None:
    from vinted_bot.services.discord_role_perms import sync_starter_perms_from_pro

    settings = get_settings()
    if not settings.discord_bot_token.strip():
        print("DISCORD_BOT_TOKEN manquant dans .env")
        return
    pro = (settings.discord_role_sub_pro or "").strip()
    starter = (settings.discord_role_sub_starter or "").strip()
    private_cat = (settings.discord_category_private_tools or "").strip()
    if not pro or not starter:
        print(
            "DISCORD_ROLE_SUB_PRO et DISCORD_ROLE_SUB_STARTER requis dans .env"
        )
        return
    dry = bool(getattr(args, "dry_run", False))
    print(
        f"{'[dry-run] ' if dry else ''}Copie perms Pro ({pro}) → Starter ({starter})"
        f"{f' — hors catégorie {private_cat}' if private_cat else ''}…"
    )
    try:
        stats = sync_starter_perms_from_pro(dry_run=dry)
    except Exception as exc:  # noqa: BLE001
        print(f"Erreur : {exc}")
        return
    log.info("sync_starter_perms_done", **stats)
    print(
        f"OK — scannés {stats['channels_scanned']} · "
        f"copiés {stats['copied']} · déjà OK {stats['already_ok']} · "
        f"exclus {stats.get('skipped_excluded', 0)} · "
        f"bloqués outils privés {stats.get('deny_view_denied', 0)} · "
        f"erreurs {stats['errors'] + stats.get('deny_view_errors', 0)}"
    )


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


def cmd_post_vintify_intro(args, log) -> None:
    from vinted_bot.interactions.discord_api import (
        DiscordInteractionClient,
        sanitize_guild_id,
    )
    from vinted_bot.interactions.vintify_intro_panel import post_vintify_intro_message

    settings = get_settings()
    if not settings.discord_bot_token.strip():
        print("DISCORD_BOT_TOKEN manquant dans .env")
        return

    with DiscordInteractionClient(settings) as client:
        channel_id = client.vintify_channel_id()
        if not channel_id:
            print(
                "DISCORD_CHANNEL_VINTIFY manquant dans .env "
                "(ID du salon Vintify)."
            )
            return
        webhook_url = getattr(settings, "discord_webhook_vintify", "") or ""
        if not webhook_url.strip():
            print(
                "DISCORD_WEBHOOK_VINTIFY manquant — crée un webhook dans le salon "
                "Vintify et copie l'URL dans .env."
            )
            return
        guild_id = sanitize_guild_id(getattr(settings, "discord_guild_id", "") or "")
        if not guild_id:
            print("DISCORD_GUILD_ID manquant dans .env")
            return
        message = post_vintify_intro_message(
            client,
            channel_id=channel_id,
            guild_id=guild_id,
            webhook_url=webhook_url,
            preview_path=getattr(args, "preview_path", None),
        )
        log.info(
            "vintify_intro_cli_posted",
            channel_id=channel_id,
            message_id=message.get("id"),
        )
        print(f"OK — intro Vintify postée dans le salon {channel_id}")


def cmd_post_subscriptions(log) -> None:
    from vinted_bot.interactions.discord_api import (
        DiscordInteractionClient,
        sanitize_guild_id,
    )
    from vinted_bot.interactions.subscriptions_panel import post_subscriptions_messages

    settings = get_settings()
    if not settings.discord_bot_token.strip():
        print("DISCORD_BOT_TOKEN manquant dans .env")
        return

    with DiscordInteractionClient(settings) as client:
        channel_id = client.subscriptions_channel_id()
        if not channel_id:
            print(
                "DISCORD_CHANNEL_SUBSCRIPTIONS manquant dans .env "
                "(ID du salon abonnements)."
            )
            return
        guild_id = sanitize_guild_id(getattr(settings, "discord_guild_id", "") or "")
        if not guild_id:
            print("DISCORD_GUILD_ID manquant dans .env")
            return
        webhook_url = getattr(settings, "discord_webhook_subscriptions", "") or ""
        messages = post_subscriptions_messages(
            client,
            channel_id=channel_id,
            guild_id=guild_id,
            webhook_url=webhook_url or None,
        )
        log.info(
            "subscriptions_cli_posted",
            channel_id=channel_id,
            count=len(messages),
        )
        print(
            f"OK — {len(messages)} messages abonnements postés dans le salon {channel_id}"
        )


def cmd_post_fiscalite(log) -> None:
    from vinted_bot.interactions.discord_api import (
        DiscordInteractionClient,
        sanitize_guild_id,
    )
    from vinted_bot.interactions.fiscalite_panel import post_fiscalite_panel

    settings = get_settings()
    if not settings.discord_bot_token.strip():
        print("DISCORD_BOT_TOKEN manquant dans .env")
        return

    with DiscordInteractionClient(settings) as client:
        channel_id = client.fiscalite_channel_id()
        if not channel_id:
            print(
                "DISCORD_CHANNEL_FISCALITE manquant dans .env "
                "(ID du salon guide-fiscalite)."
            )
            return
        guild_id = sanitize_guild_id(getattr(settings, "discord_guild_id", "") or "")
        if not guild_id:
            print("DISCORD_GUILD_ID manquant dans .env")
            return
        webhook_url = getattr(settings, "discord_webhook_fiscalite", "") or ""
        message = post_fiscalite_panel(
            client,
            channel_id=channel_id,
            guild_id=guild_id,
            webhook_url=webhook_url or None,
        )
        log.info(
            "fiscalite_cli_posted",
            channel_id=channel_id,
            message_id=message.get("id"),
        )
        print(f"OK — guide fiscalité posté dans le salon {channel_id}")


def cmd_post_recruitment(log) -> None:
    from vinted_bot.interactions.discord_api import (
        DiscordInteractionClient,
        sanitize_guild_id,
    )
    from vinted_bot.interactions.recruitment_panel import (
        PANEL_TITLE,
        build_recruitment_panel_payload,
    )

    settings = get_settings()
    if not settings.discord_bot_token.strip():
        print("DISCORD_BOT_TOKEN manquant dans .env")
        return

    with DiscordInteractionClient(settings) as client:
        channel_id = client.recruitment_channel_id()
        if not channel_id:
            print(
                "DISCORD_CHANNEL_RECRUITMENT manquant dans .env "
                "(ID du salon ticket-recrutement)."
            )
            return
        guild_id = sanitize_guild_id(getattr(settings, "discord_guild_id", "") or "")
        if not guild_id:
            print("DISCORD_GUILD_ID manquant dans .env")
            return

        # Purge anciens panneaux bot / webhook
        from vinted_bot.config import discord_application_id
        from vinted_bot.interactions.discord_api import parse_discord_webhook_url
        from vinted_bot.interactions.recruitment_panel import RECRUIT_OPEN

        bot_id = discord_application_id(settings.discord_bot_token)
        webhook_url = getattr(settings, "discord_webhook_recruitment", "") or ""
        parsed = parse_discord_webhook_url(webhook_url)
        old_titles = {
            PANEL_TITLE,
            "📋 Recrutement staff Resello",
            "🚀 Recrutement staff Resello",
        }
        try:
            msgs = client.list_channel_messages(channel_id, limit=30)
            for msg in msgs:
                embeds = msg.get("embeds") or []
                title = embeds[0].get("title") if embeds else ""
                author = msg.get("author") or {}
                components = msg.get("components") or []
                has_open_btn = False
                for row in components:
                    for comp in row.get("components") or []:
                        if str(comp.get("custom_id") or "") == RECRUIT_OPEN:
                            has_open_btn = True
                            break
                is_panel = title in old_titles or has_open_btn
                is_bot = bot_id and str(author.get("id")) == bot_id
                wid = str(msg.get("webhook_id") or "")
                if not is_panel and not (is_bot and embeds):
                    continue
                if parsed and (wid == parsed[0] or is_panel):
                    try:
                        del_resp = client._client.delete(
                            f"/webhooks/{parsed[0]}/{parsed[1]}/messages/{msg['id']}"
                        )
                        if del_resp.status_code < 400 or del_resp.status_code == 404:
                            if del_resp.status_code < 400:
                                continue
                    except Exception:  # noqa: BLE001
                        pass
                try:
                    client.delete_channel_message(channel_id, str(msg["id"]))
                except Exception:  # noqa: BLE001
                    pass
        except Exception as exc:  # noqa: BLE001
            log.warning("recruitment_panel_purge_failed", error=str(exc)[:160])

        payload = build_recruitment_panel_payload()
        message = _post_recruitment_panel_branded(
            client,
            channel_id=channel_id,
            guild_id=guild_id,
            payload=payload,
            webhook_url=webhook_url or None,
            log=log,
        )
        log.info(
            "recruitment_panel_posted",
            channel_id=channel_id,
            message_id=message.get("id"),
        )
        missing = []
        if not client.recruitment_category_id():
            missing.append("DISCORD_CATEGORY_RECRUITMENT_TICKETS")
        if not client.recruitment_staff_role_id():
            missing.append("DISCORD_ROLE_RECRUITMENT_STAFF")
        print(f"OK — panneau recrutement posté dans le salon {channel_id}")
        if missing:
            print(
                "⚠ Configure aussi dans .env : " + ", ".join(missing)
            )


def _post_recruitment_panel_branded(
    client,
    *,
    channel_id: str,
    guild_id: str,
    payload: dict,
    webhook_url: str | None,
    log,
) -> dict:
    """Poste le panneau avec le logo serveur Resello (pas l'avatar bot)."""
    from vinted_bot.interactions.discord_api import parse_discord_webhook_url
    from vinted_bot.interactions.recruitment_panel import RECRUIT_OPEN

    def _has_open_btn(msg: dict) -> bool:
        return any(
            str(c.get("custom_id") or "") == RECRUIT_OPEN
            for row in (msg.get("components") or [])
            for c in (row.get("components") or [])
        )

    parsed = parse_discord_webhook_url(webhook_url or "")
    if parsed:
        # Avatar / nom = serveur Resello via le webhook salon
        embed_only = {
            "embeds": payload.get("embeds") or [],
        }
        branded = client.post_channel_payload_as_guild(
            channel_id,
            embed_only,
            guild_id=guild_id,
            webhook_url=webhook_url,
        )
        # Les webhooks salon (application_id null) ne portent pas les boutons
        btn_msg = client.post_channel_payload(
            channel_id,
            {
                "content": "Clique pour candidater :",
                "components": payload.get("components") or [],
            },
        )
        log.info(
            "recruitment_panel_branded_webhook",
            embed_message_id=branded.get("id"),
            button_message_id=btn_msg.get("id"),
        )
        return branded

    # Sans webhook : tente webhook bot (logo + bouton), sinon bot + logo embed
    message = client.post_channel_payload_as_guild(
        channel_id,
        payload,
        guild_id=guild_id,
        webhook_url=None,
    )
    if _has_open_btn(message) and message.get("webhook_id"):
        return message
    if not _has_open_btn(message):
        try:
            if message.get("id"):
                client.delete_channel_message(channel_id, str(message["id"]))
        except Exception:  # noqa: BLE001
            pass
        return client.post_channel_payload_with_guild_logo(
            channel_id, payload, guild_id=guild_id
        )
    return message


def cmd_post_support(log) -> None:
    from vinted_bot.interactions.discord_api import (
        DiscordInteractionClient,
        parse_discord_webhook_url,
        sanitize_guild_id,
    )
    from vinted_bot.interactions.support_panel import (
        PANEL_TITLE,
        SUPPORT_OPEN,
        build_support_panel_payload,
    )
    from vinted_bot.config import discord_application_id

    settings = get_settings()
    if not settings.discord_bot_token.strip():
        print("DISCORD_BOT_TOKEN manquant dans .env")
        return

    with DiscordInteractionClient(settings) as client:
        channel_id = client.support_channel_id()
        if not channel_id:
            print(
                "DISCORD_CHANNEL_SUPPORT manquant dans .env "
                "(ID du salon ticket-aide)."
            )
            return
        guild_id = sanitize_guild_id(getattr(settings, "discord_guild_id", "") or "")
        if not guild_id:
            print("DISCORD_GUILD_ID manquant dans .env")
            return

        bot_id = discord_application_id(settings.discord_bot_token)
        webhook_url = getattr(settings, "discord_webhook_support", "") or ""
        parsed = parse_discord_webhook_url(webhook_url)
        old_titles = {PANEL_TITLE, "🆘 Besoin d'aide ?"}
        try:
            msgs = client.list_channel_messages(channel_id, limit=30)
            for msg in msgs:
                embeds = msg.get("embeds") or []
                title = embeds[0].get("title") if embeds else ""
                author = msg.get("author") or {}
                components = msg.get("components") or []
                has_open_btn = any(
                    str(c.get("custom_id") or "") == SUPPORT_OPEN
                    for row in components
                    for c in (row.get("components") or [])
                )
                is_panel = title in old_titles or has_open_btn
                is_bot = bot_id and str(author.get("id")) == bot_id
                wid = str(msg.get("webhook_id") or "")
                empty = not embeds and not components and not (msg.get("content") or "").strip()
                if not is_panel and not empty and not (is_bot and embeds):
                    if not (parsed and wid == parsed[0]):
                        continue
                if parsed and (wid == parsed[0] or is_panel or empty):
                    try:
                        del_resp = client._client.delete(
                            f"/webhooks/{parsed[0]}/{parsed[1]}/messages/{msg['id']}"
                        )
                        if del_resp.status_code < 400:
                            continue
                    except Exception:  # noqa: BLE001
                        pass
                try:
                    client.delete_channel_message(channel_id, str(msg["id"]))
                except Exception:  # noqa: BLE001
                    pass
        except Exception as exc:  # noqa: BLE001
            log.warning("support_panel_purge_failed", error=str(exc)[:160])

        payload = build_support_panel_payload()
        # Bot + logo serveur dans l'embed (les webhooks salon perdent embeds/boutons ici)
        message = client.post_channel_payload_with_guild_logo(
            channel_id,
            payload,
            guild_id=guild_id,
        )
        log.info(
            "support_panel_posted",
            channel_id=channel_id,
            message_id=message.get("id"),
        )
        missing = []
        if not client.support_category_id():
            missing.append("DISCORD_CATEGORY_RECRUITMENT_TICKETS (ou SUPPORT)")
        if not client.support_staff_role_id():
            missing.append("DISCORD_ROLE_RECRUITMENT_STAFF (ou SUPPORT)")
        print(f"OK — panneau aide posté dans le salon {channel_id}")
        if missing:
            print("⚠ Configure aussi dans .env : " + ", ".join(missing))


def cmd_post_fournisseurs(log) -> None:
    from vinted_bot.interactions.discord_api import (
        DiscordInteractionClient,
        sanitize_guild_id,
    )
    from vinted_bot.interactions.fournisseurs_panel import (
        FLEEK_BANNER_FILENAME,
        PANEL_TITLE,
        build_fleek_panel_payload,
        resolve_fleek_banner_path,
    )
    from vinted_bot.config import discord_application_id

    settings = get_settings()
    if not settings.discord_bot_token.strip():
        print("DISCORD_BOT_TOKEN manquant dans .env")
        return

    banner_path = resolve_fleek_banner_path(
        getattr(settings, "fleek_banner_path", "") or "config/fleek-banner.png"
    )
    if not banner_path.is_file():
        print(f"Bannière Fleek introuvable : {banner_path}")
        return
    banner_bytes = banner_path.read_bytes()

    with DiscordInteractionClient(settings) as client:
        channel_id = client.fournisseurs_channel_id()
        if not channel_id:
            print(
                "DISCORD_CHANNEL_FOURNISSEURS manquant dans .env "
                "(ID du salon fournisseurs)."
            )
            return
        guild_id = sanitize_guild_id(getattr(settings, "discord_guild_id", "") or "")
        bot_id = discord_application_id(settings.discord_bot_token)

        # Purge anciens panneaux Fleek
        try:
            for msg in client.list_channel_messages(channel_id, limit=30):
                embeds = msg.get("embeds") or []
                title = embeds[0].get("title") if embeds else ""
                author = msg.get("author") or {}
                is_bot = bot_id and str(author.get("id")) == bot_id
                if title == PANEL_TITLE or (
                    is_bot and "Fleek" in str(title)
                ):
                    try:
                        client.delete_channel_message(channel_id, str(msg["id"]))
                    except Exception:  # noqa: BLE001
                        pass
        except Exception as exc:  # noqa: BLE001
            log.warning("fournisseurs_panel_purge_failed", error=str(exc)[:160])

        payload = build_fleek_panel_payload(banner_filename=FLEEK_BANNER_FILENAME)
        # Les webhooks salon se font vider (embeds) sur ce serveur → bot + logo embed.
        # Avatar bot = logo Resello ; thumbnail embed = logo serveur.
        if guild_id:
            message = client.post_channel_payload_with_attachments(
                channel_id,
                payload,
                attachments=[(FLEEK_BANNER_FILENAME, banner_bytes, "image/png")],
            )
            # Enrichir avec logo serveur si possible (edit)
            try:
                guild_name, _, logo_url = client.fetch_guild_branding(guild_id)
                if logo_url and message.get("id"):
                    embeds = list(payload.get("embeds") or [])
                    if embeds:
                        embeds = client._apply_guild_logo_to_intro(
                            embeds,
                            guild_name=guild_name,
                            icon_url=logo_url,
                        )
                        # Garder l'image bannière CDN du message posté
                        atts = message.get("attachments") or []
                        if atts:
                            embeds[0]["image"] = {
                                "url": atts[0].get("url") or atts[0].get("proxy_url")
                            }
                        client._client.patch(
                            f"/channels/{channel_id}/messages/{message['id']}",
                            json={"embeds": embeds},
                        )
            except Exception as exc:  # noqa: BLE001
                log.warning("fournisseurs_logo_enrich_failed", error=str(exc)[:160])
        else:
            message = client.post_channel_payload_with_attachments(
                channel_id,
                payload,
                attachments=[(FLEEK_BANNER_FILENAME, banner_bytes, "image/png")],
            )
        log.info(
            "fournisseurs_panel_posted",
            channel_id=channel_id,
            message_id=message.get("id"),
        )
        print(f"OK — panneau Fleek posté dans le salon {channel_id}")


def cmd_discord_interactions(log) -> None:
    from vinted_bot.interactions.gateway import run_discord_interactions

    settings = get_settings()
    if not settings.discord_bot_token.strip():
        print("DISCORD_BOT_TOKEN manquant dans .env")
        return

    print(
        "Écoute Discord (filtres + alertes) — Ctrl+C pour arrêter.\n"
        "Webhook Whop : POST /webhooks/whop (PORT Railway ou WHOP_WEBHOOK_PORT).\n"
        "Prod : docs/RAILWAY_WHOP.md — URL fixe https://<service>.up.railway.app/webhooks/whop\n"
        "Lance en parallèle (local) : uv run vinted-bot scrape --loop"
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
    if args.command == "post-reglement":
        cmd_post_reglement(args, log)
        return
    if args.command == "setup-reglement-gates":
        cmd_setup_reglement_gates(log)
        return
    if args.command == "sync-proplus-perms":
        cmd_sync_proplus_perms(args, log)
        return
    if args.command == "sync-starter-perms":
        cmd_sync_starter_perms(args, log)
        return
    if args.command == "post-detector-apercu":
        cmd_post_detector_apercu(log)
        return
    if args.command == "post-niches-vinted-intro":
        cmd_post_niches_vinted_intro(args, log)
        return
    if args.command == "post-vintify-intro":
        cmd_post_vintify_intro(args, log)
        return
    if args.command == "post-subscriptions":
        cmd_post_subscriptions(log)
        return
    if args.command == "post-fiscalite":
        cmd_post_fiscalite(log)
        return
    if args.command == "post-recruitment":
        cmd_post_recruitment(log)
        return
    if args.command == "post-support":
        cmd_post_support(log)
        return
    if args.command == "post-fournisseurs":
        cmd_post_fournisseurs(log)
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

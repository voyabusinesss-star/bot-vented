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
        required=True,
        help='Texte de recherche (ex: "nike air max")',
    )
    scrape.add_argument(
        "--once",
        action="store_true",
        help="Exécute un seul passage (MVP Phase 2)",
    )
    scrape.add_argument(
        "--max-items",
        type=int,
        default=24,
        help="Nombre max d'annonces à récupérer (défaut: 24)",
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
        listing = upsert_listing(
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
        again = upsert_listing(
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
    if not args.once:
        print("Pour la Phase 2, utilise --once (ex: scrape --query nike --once)")
        return

    from vinted_bot.services.scrape_search import scrape_search_once

    settings = get_settings()
    headless = not args.headed and settings.scrape_headless
    result = scrape_search_once(
        args.query,
        max_items=args.max_items,
        headless=headless,
    )
    log.info(
        "scrape_done",
        query=result.query,
        found=result.items_found,
        upserted=result.items_upserted,
        posted_discord=result.items_posted_discord,
        run_id=result.scrape_run_id,
    )
    print(
        f"OK — query={result.query!r} "
        f"found={result.items_found} upserted={result.items_upserted} "
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

    parser.print_help()


if __name__ == "__main__":
    main()

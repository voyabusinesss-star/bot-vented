"""Filtres privés : matching annonces + alertes DM Discord."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Sequence

from vinted_bot.config import get_settings
from vinted_bot.db.models import Listing, UserFilter
from vinted_bot.db.session import session_scope
from vinted_bot.db.user_filters import (
    already_alerted,
    list_all_active_filters,
    record_filter_alert,
)
from vinted_bot.notify.discord import normalize_brand
from vinted_bot.utils.logging import get_logger

log = get_logger(__name__)

EMBED_COLOR = 0x5865F2

# Synonymes catégories (filtres utilisateurs → titres / slugs DB)
_CATEGORY_ALIASES: dict[str, tuple[str, ...]] = {
    "chaussure": (
        "chaussure",
        "chaussures",
        "shoe",
        "shoes",
        "sneaker",
        "sneakers",
        "basket",
        "baskets",
        "tn",
        "dunk",
        "jordan",
        "yeezy",
    ),
    "veste": ("veste", "jacket", "blouson", "coat", "manteau"),
    "hoodie": ("hoodie", "sweat", "hooded"),
    "pantalon": ("pantalon", "pants", "jean", "cargo"),
}


def _fold(text: str | None) -> str:
    if not text:
        return ""
    raw = unicodedata.normalize("NFKD", str(text))
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    return " ".join(raw.lower().replace("-", " ").replace("_", " ").split())


def _contains(haystack: str, needle: str) -> bool:
    n = _fold(needle)
    h = _fold(haystack)
    return bool(n) and n in h


def listing_matches_filter(listing: Listing, filt: UserFilter) -> bool:
    """True si l'annonce satisfait TOUS les critères renseignés du filtre."""
    title = listing.title or ""
    brand = listing.brand or ""
    model = listing.model_slug or ""
    category = listing.category_slug or ""
    blob = f"{title} {brand} {model} {category}"

    if filt.brand:
        listing_brand = normalize_brand(brand) or _fold(brand)
        want = normalize_brand(filt.brand) or _fold(filt.brand)
        if want and want not in listing_brand and want not in _fold(title):
            return False

    if filt.model:
        if not (
            _contains(model, filt.model)
            or _contains(title, filt.model)
            or _contains(blob, filt.model)
        ):
            return False

    if filt.category:
        cat = _fold(filt.category)
        # Normaliser pluriels courants (chaussures → chaussure)
        if cat.endswith("s") and len(cat) > 3:
            cat_stem = cat[:-1]
        else:
            cat_stem = cat
        aliases = _CATEGORY_ALIASES.get(cat) or _CATEGORY_ALIASES.get(cat_stem) or (
            cat,
            cat_stem,
        )
        blob_f = _fold(blob)
        cat_f = _fold(category)
        if not any(a in blob_f or a in cat_f for a in aliases if a):
            return False

    if filt.keyword:
        if not _contains(blob, filt.keyword):
            return False

    price_eur = None
    if listing.price_cents is not None:
        price_eur = listing.price_cents / 100.0
    if filt.max_price_eur is not None:
        if price_eur is None or price_eur > float(filt.max_price_eur) + 1e-6:
            return False
    if filt.min_price_eur is not None:
        if price_eur is None or price_eur < float(filt.min_price_eur) - 1e-6:
            return False

    return True


@dataclass(frozen=True, slots=True)
class PrivateMatch:
    filter_id: int
    discord_user_id: int
    listing: Listing
    signal: str
    market_eur: float | None
    display_number: int = 1


def _estimate_market_eur(listing: Listing) -> float | None:
    """Proxy valeur marché (deal filter si dispo, sinon ~2× prix)."""
    try:
        from vinted_bot.services.deal_filter import evaluate_listing, load_deal_filters

        deal = evaluate_listing(listing, config=load_deal_filters())
        resell = getattr(deal, "resell_estimate_eur", None) or getattr(
            deal, "resell_eur", None
        )
        if resell is not None:
            return float(resell)
        resell_cents = getattr(deal, "resell_cents", None)
        if resell_cents is not None:
            return float(resell_cents) / 100.0
    except Exception:  # noqa: BLE001
        pass
    if listing.price_cents is None:
        return None
    return round((listing.price_cents / 100.0) * 2.2, 0)


def _signal_for_listing(listing: Listing, market_eur: float | None) -> str:
    price = (listing.price_cents or 0) / 100.0
    bits: list[str] = []
    if market_eur and price > 0 and market_eur >= price * 1.35:
        bits.append("Sous-évalué")
    # Publié récemment
    raw = listing.raw_json if isinstance(listing.raw_json, dict) else {}
    created = raw.get("created_at_ts") or raw.get("created_at")
    recent = False
    if isinstance(created, (int, float)):
        age_h = (
            datetime.now(timezone.utc).timestamp() - float(created)
        ) / 3600.0
        recent = age_h <= 6
    elif listing.first_seen_at is not None:
        fs = listing.first_seen_at
        if fs.tzinfo is None:
            fs = fs.replace(tzinfo=timezone.utc)
        recent = datetime.now(timezone.utc) - fs <= timedelta(hours=6)
    if recent:
        bits.append("Publié récemment")
    if not bits:
        bits.append("Correspond à ton filtre")
    return " · ".join(bits[:2])


def _photo_url(listing: Listing) -> str | None:
    for photo in listing.photos or []:
        url = getattr(photo, "url", None)
        if url:
            return str(url)
    raw = listing.raw_json if isinstance(listing.raw_json, dict) else {}
    photo = raw.get("photo")
    if isinstance(photo, dict):
        for key in ("full_size_url", "url"):
            if photo.get(key):
                return str(photo[key])
    return None


def _published_display(listing: Listing) -> str:
    """Date d'ajout lisible : absolue (Paris) + relatif."""
    from zoneinfo import ZoneInfo

    from vinted_bot.notify.discord import _published_label

    raw = listing.raw_json if isinstance(listing.raw_json, dict) else {}
    created: Any = raw.get("created_at_ts") or raw.get("created_at")
    dt: datetime | None = None
    if listing.published_at is not None:
        dt = listing.published_at
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    elif isinstance(created, (int, float)):
        dt = datetime.fromtimestamp(float(created), tz=timezone.utc)
    elif isinstance(created, str):
        try:
            dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
        except ValueError:
            dt = None
    if dt is None and listing.first_seen_at is not None:
        dt = listing.first_seen_at
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

    relative = _published_label(listing)
    if dt is None:
        return f"⌛ **Ajoutée :** {relative}"
    local = dt.astimezone(ZoneInfo("Europe/Paris"))
    absolute = local.strftime("%d/%m/%Y %H:%M")
    return f"⌛ **Ajoutée :** {absolute} ({relative})"


def build_private_alert_embed(match: PrivateMatch) -> dict[str, Any]:
    listing = match.listing
    price = (
        f"{listing.price_cents / 100.0:.0f} €"
        if listing.price_cents is not None
        else "—"
    )
    market = (
        f"{match.market_eur:.0f} €" if match.market_eur is not None else "—"
    )
    title = (listing.title or "Annonce Vinted")[:256]
    url = (listing.url or "").strip()
    brand = getattr(listing, "brand", None) or "—"
    description = (
        f"{_published_display(listing)}\n\n"
        f"💰 **Prix :** {price}\n"
        f"🔖 **Marque :** {brand}\n"
        f"📈 **Valeur marché :** {market}\n"
        f"🔥 **Signal :** {match.signal}"
    )
    embed: dict[str, Any] = {
        "title": title,
        "description": description[:3900],
        "color": EMBED_COLOR,
        "footer": {
            "text": (
                f"Filtre #{getattr(match, 'display_number', match.filter_id)} "
                "· privé · Resello"
            )
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if url:
        embed["url"] = url
    if listing.published_at is not None:
        pub = listing.published_at
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=timezone.utc)
        embed["timestamp"] = pub.isoformat()
    photo = _photo_url(listing)
    if photo:
        embed["image"] = {"url": photo}
    return embed


def build_private_alert_payload(match: PrivateMatch) -> dict[str, Any]:
    """Embed + boutons Détails / Négocier / Acheter."""
    listing = match.listing
    url = (listing.url or "").strip() or "https://www.vinted.fr"
    components = [
        {
            "type": 1,
            "components": [
                {"type": 2, "style": 5, "label": "📄 Détails", "url": url},
                {"type": 2, "style": 5, "label": "🤝 Négocier", "url": url},
                {"type": 2, "style": 5, "label": "💳 Acheter", "url": url},
            ],
        }
    ]
    return {
        "embeds": [build_private_alert_embed(match)],
        "components": components,
    }


def _listing_age_seconds(listing: Listing) -> float | None:
    """Âge estimé de l'annonce (publication ou première vue)."""
    now = datetime.now(timezone.utc)
    candidates: list[datetime] = []
    for attr in (listing.published_at, listing.first_seen_at):
        if attr is None:
            continue
        ts = attr if attr.tzinfo else attr.replace(tzinfo=timezone.utc)
        candidates.append(ts)
    raw = listing.raw_json if isinstance(listing.raw_json, dict) else {}
    created_ts = raw.get("created_at_ts") or raw.get("photo", {})
    if isinstance(created_ts, dict):
        created_ts = raw.get("created_at_ts")
    if isinstance(created_ts, (int, float)) and created_ts > 0:
        candidates.append(datetime.fromtimestamp(float(created_ts), tz=timezone.utc))
    if not candidates:
        return None
    newest = max(candidates)  # timestamp le plus récent = référence fraîcheur
    # Pour l'âge on prend le plus ancien signal de "naissance" utile :
    # published_at si dispo, sinon first_seen
    birth = listing.published_at or listing.first_seen_at
    if birth is not None:
        b = birth if birth.tzinfo else birth.replace(tzinfo=timezone.utc)
        return max(0.0, (now - b).total_seconds())
    return max(0.0, (now - newest).total_seconds())


def is_fresh_listing(
    listing: Listing,
    *,
    max_age_seconds: float,
) -> bool:
    """True si l'annonce vient d'être postée sur Vinted (pas un vieux stock)."""
    now = datetime.now(timezone.utc)

    published = listing.published_at
    pub_age: float | None = None
    if published is not None:
        ts = published if published.tzinfo else published.replace(tzinfo=timezone.utc)
        pub_age = (now - ts).total_seconds()
        if pub_age <= max_age_seconds:
            return True

    raw = listing.raw_json if isinstance(listing.raw_json, dict) else {}
    created_ts = raw.get("created_at_ts")
    if isinstance(created_ts, (int, float)) and created_ts > 0:
        ts = datetime.fromtimestamp(float(created_ts), tz=timezone.utc)
        age = (now - ts).total_seconds()
        if age <= max_age_seconds:
            return True
        # Date Vinted connue et trop vieille → jamais de backfill
        return False

    if pub_age is not None:
        # published_at connu et trop vieux → pas d'alerte
        return False

    # Pas de date Vinted : se fier à la 1ère vue bot (nouveauté pour nous)
    first_seen = listing.first_seen_at
    if first_seen is not None:
        ts = first_seen if first_seen.tzinfo else first_seen.replace(tzinfo=timezone.utc)
        if (now - ts).total_seconds() <= max_age_seconds:
            return True
        return False

    return True


def find_private_matches(listings: Sequence[Listing]) -> list[PrivateMatch]:
    if not listings:
        return []
    settings = get_settings()
    max_age = float(
        getattr(settings, "private_filter_max_age_seconds", 180.0) or 180.0
    )
    matches: list[PrivateMatch] = []
    with session_scope() as session:
        filters = list_all_active_filters(session)
        if not filters:
            return []
        # Numéros affichés par user (tous filtres, ordre id)
        from collections import defaultdict

        from vinted_bot.db.user_filters import list_user_filters

        ordinals: dict[tuple[int, int], int] = {}
        by_user: dict[int, list[int]] = defaultdict(list)
        for f in filters:
            by_user[int(f.discord_user_id)].append(int(f.id))
        for uid in by_user:
            for idx, row in enumerate(list_user_filters(session, uid), start=1):
                ordinals[(uid, int(row.id))] = idx
        # détacher filtres
        filt_data = [
            (
                f.id,
                f.discord_user_id,
                f.brand,
                f.model,
                f.category,
                f.keyword,
                f.max_price_eur,
                f.min_price_eur,
                f.is_active,
            )
            for f in filters
        ]

    # Reconstruire objets légers pour matching hors session filtres
    class _F:
        pass

    rebuilt: list[tuple[Any, int, int]] = []
    for (
        fid,
        uid,
        brand,
        model,
        category,
        keyword,
        max_p,
        min_p,
        active,
    ) in filt_data:
        if not active:
            continue
        f = _F()
        f.id = fid
        f.discord_user_id = uid
        f.brand = brand
        f.model = model
        f.category = category
        f.keyword = keyword
        f.max_price_eur = max_p
        f.min_price_eur = min_p
        rebuilt.append((f, fid, uid))

    for listing in listings:
        if not is_fresh_listing(listing, max_age_seconds=max_age):
            continue
        for f, fid, uid in rebuilt:
            if not listing_matches_filter(listing, f):  # type: ignore[arg-type]
                continue
            with session_scope() as session:
                if already_alerted(
                    session, filter_id=fid, vinted_id=int(listing.vinted_id)
                ):
                    continue
            market = _estimate_market_eur(listing)
            matches.append(
                PrivateMatch(
                    filter_id=fid,
                    discord_user_id=uid,
                    listing=listing,
                    signal=_signal_for_listing(listing, market),
                    market_eur=market,
                    display_number=ordinals.get((uid, fid), 1),
                )
            )
    return matches


def send_private_filter_alerts(listings: Sequence[Listing]) -> int:
    """Évalue les nouvelles annonces et envoie les DM privés. Retourne # DM envoyés."""
    import time

    settings = get_settings()
    if not settings.discord_bot_token.strip():
        return 0
    matches = find_private_matches(listings)
    if not matches:
        return 0

    # Plus récentes d'abord
    def _sort_key(m: PrivateMatch) -> float:
        age = _listing_age_seconds(m.listing)
        return age if age is not None else 0.0

    matches.sort(key=_sort_key)
    max_dm = int(getattr(settings, "private_filter_max_dm_per_scrape", 3) or 3)
    matches = matches[: max(1, max_dm)]
    delay = float(getattr(settings, "private_filter_dm_delay_seconds", 60.0) or 0.0)

    from vinted_bot.notify.discord import DiscordNotifier

    sent = 0
    with DiscordNotifier(settings) as notifier:
        for idx, match in enumerate(matches):
            if idx > 0 and delay > 0:
                log.info(
                    "private_filter_dm_delay",
                    seconds=delay,
                    next_vinted_id=match.listing.vinted_id,
                )
                time.sleep(delay)
            payload = build_private_alert_payload(match)
            title = (match.listing.title or "Annonce")[:80]
            try:
                notifier.send_dm_payload(
                    match.discord_user_id,
                    {
                        **payload,
                        "content": f"🔔 [{title}]({match.listing.url})"
                        if match.listing.url
                        else f"🔔 **{title}**",
                    },
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "private_filter_dm_failed",
                    discord_user_id=match.discord_user_id,
                    filter_id=match.filter_id,
                    vinted_id=match.listing.vinted_id,
                    error=str(exc)[:160],
                )
                continue
            with session_scope() as session:
                record_filter_alert(
                    session,
                    filter_id=match.filter_id,
                    discord_user_id=match.discord_user_id,
                    vinted_id=int(match.listing.vinted_id),
                )
            sent += 1
            log.info(
                "private_filter_dm_sent",
                discord_user_id=match.discord_user_id,
                filter_id=match.filter_id,
                vinted_id=match.listing.vinted_id,
            )
    return sent


def _filter_display_title(row: UserFilter) -> str:
    if row.name and str(row.name).strip():
        return str(row.name).strip()
    bits = [x for x in (row.brand, row.model) if x]
    if bits:
        return " ".join(bits)
    if row.keyword:
        return str(row.keyword)
    return "Filtre personnalisé"


def build_filter_created_embed(
    *,
    display_number: int,
    row: UserFilter,
) -> dict[str, Any]:
    """Embed DM Resello à la création d'une alerte privée."""
    criteria: list[str] = []
    if row.brand:
        criteria.append(f"🏷️ Marque : {row.brand}")
    if row.model:
        criteria.append(f"🔎 Modèle : {row.model}")
    if row.category:
        criteria.append(f"📂 Catégorie : {row.category}")
    if row.keyword:
        criteria.append(f"💬 Mot-clé : {row.keyword}")
    if row.min_price_eur is not None:
        criteria.append(f"💰 Prix minimum : {row.min_price_eur:.0f} €")
    if row.max_price_eur is not None:
        criteria.append(f"💰 Prix maximum : {row.max_price_eur:.0f} €")
    criteria_block = "\n".join(criteria) if criteria else "_Aucun critère détaillé_"

    settings = get_settings()
    channel_id = (getattr(settings, "discord_channel_mes_alertes", "") or "").strip()
    manage_line = (
        f"⚙️ <#{channel_id}>"
        if channel_id
        else "⚙️ **MES ALERTES**"
    )
    title = _filter_display_title(row)

    description = (
        "Ton assistant **Resello** surveille maintenant le marché pour toi **24/7**.\n\n"
        "━━━━━━━━━━━━━━\n\n"
        f"**🎯 Filtre #{display_number} créé**\n\n"
        f"👟 **{title}**\n\n"
        f"**Critères :**\n{criteria_block}\n\n"
        "━━━━━━━━━━━━━━\n\n"
        "**⚡ Fonctionnement**\n\n"
        "Dès qu'une **nouvelle** annonce correspond à tes critères, "
        "tu recevras automatiquement une alerte privée avec :\n\n"
        "📸 Photo du produit\n"
        "💰 Prix trouvé\n"
        "📈 Valeur estimée du marché\n"
        "🔥 Potentiel achat/revente\n"
        "🔗 Lien direct vers l'annonce\n\n"
        "━━━━━━━━━━━━━━\n\n"
        "🟢 **Statut :** Surveillance active\n"
        "🔎 Alertes uniquement sur les **nouvelles** annonces.\n\n"
        "━━━━━━━━━━━━━━\n\n"
        f"Tu peux gérer tes filtres à tout moment depuis :\n{manage_line}"
    )
    return {
        "title": "🔔 Alerte personnalisée activée",
        "description": description[:4096],
        "color": EMBED_COLOR,
        "footer": {"text": f"Filtre #{display_number} · privé · Resello"},
    }


def notify_filter_created(
    *,
    discord_user_id: int,
    filter_id: int,
    filter_summary: str | None = None,  # noqa: ARG001 — compat
) -> tuple[bool, str | None]:
    """Compat : synchronise le DM Resello unique (plus de DM « créé » séparé)."""
    from vinted_bot.services.resello_dm import sync_resello_filters_dm

    return sync_resello_filters_dm(discord_user_id=discord_user_id)


def scan_recent_matches_for_filter(
    *,
    filter_id: int,
    discord_user_id: int,
    lookback_hours: float = 48.0,
    limit: int = 3,
) -> int:
    """Scan immédiat des annonces récentes pour ce filtre → DM (max `limit`)."""
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from vinted_bot.db.user_filters import get_user_filter

    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    with session_scope() as session:
        filt = get_user_filter(
            session, filter_id=filter_id, discord_user_id=discord_user_id
        )
        if filt is None or not filt.is_active:
            return 0
        # Matérialiser critères
        f_data = SimpleNamespace(
            brand=filt.brand,
            model=filt.model,
            category=filt.category,
            keyword=filt.keyword,
            max_price_eur=filt.max_price_eur,
            min_price_eur=filt.min_price_eur,
        )
        rows = list(
            session.scalars(
                select(Listing)
                .options(selectinload(Listing.photos))
                .where(Listing.is_active.is_(True))
                .where(
                    (Listing.first_seen_at >= cutoff)
                    | (Listing.last_seen_at >= cutoff)
                    | (Listing.scraped_at >= cutoff)
                )
                .order_by(Listing.last_seen_at.desc().nullslast())
                .limit(400)
            )
            .unique()
            .all()
        )
        matched_listings: list[Listing] = []
        for listing in rows:
            if listing_matches_filter(listing, f_data):  # type: ignore[arg-type]
                if already_alerted(
                    session, filter_id=filter_id, vinted_id=int(listing.vinted_id)
                ):
                    continue
                for photo in list(listing.photos):
                    session.expunge(photo)
                session.expunge(listing)
                matched_listings.append(listing)
                if len(matched_listings) >= limit:
                    break

    if not matched_listings:
        return 0

    # Réutiliser le pipeline d'envoi en forçant ce filtre uniquement
    settings = get_settings()
    from vinted_bot.notify.discord import DiscordNotifier

    sent = 0
    with DiscordNotifier(settings) as notifier:
        for listing in matched_listings:
            market = _estimate_market_eur(listing)
            match = PrivateMatch(
                filter_id=filter_id,
                discord_user_id=discord_user_id,
                listing=listing,
                signal=_signal_for_listing(listing, market),
                market_eur=market,
            )
            try:
                payload = build_private_alert_payload(match)
                title = (listing.title or "Annonce")[:80]
                notifier.send_dm_payload(
                    discord_user_id,
                    {
                        **payload,
                        "content": (
                            f"🔔 [{title}]({listing.url})"
                            if listing.url
                            else f"🔔 **{title}**"
                        ),
                    },
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "private_filter_backfill_dm_failed",
                    error=str(exc)[:160],
                    vinted_id=listing.vinted_id,
                )
                continue
            with session_scope() as session:
                record_filter_alert(
                    session,
                    filter_id=filter_id,
                    discord_user_id=discord_user_id,
                    vinted_id=int(listing.vinted_id),
                )
            sent += 1
    return sent

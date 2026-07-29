"""Handlers des filtres privés et alertes Discord."""

from __future__ import annotations

from typing import Any

from vinted_bot.config import get_settings
from vinted_bot.db.session import session_scope
from vinted_bot.interactions.alerts_panel import (
    ALERT_CREATE,
    ALERT_CREATE_MODAL,
    ALERT_DELETE_SELECT,
    ALERT_EDIT_MODAL_PREFIX,
    ALERT_EDIT_SELECT,
    ALERT_LIST,
    ALERT_PAUSE_SELECT,
    build_create_alert_modal,
    build_edit_alert_modal,
    build_user_alerts_payload,
)
from vinted_bot.interactions.reglement_panel import REGLEMENT_ACCEPT
from vinted_bot.interactions.discord_api import DiscordInteractionClient
from vinted_bot.utils.logging import get_logger

log = get_logger(__name__)


def _interaction_user(interaction: dict[str, Any]) -> dict[str, Any]:
    member = interaction.get("member") or {}
    user = member.get("user") or interaction.get("user") or {}
    return user


def _discord_display_name(user: dict[str, Any]) -> str:
    return str(user.get("global_name") or user.get("username") or "Membre")


def _option_map(interaction: dict[str, Any]) -> dict[str, Any]:
    options = interaction.get("data", {}).get("options") or []
    out: dict[str, Any] = {}
    for opt in options:
        if isinstance(opt, dict) and "name" in opt:
            out[str(opt["name"])] = opt.get("value")
    return out


def _is_filter_admin(user_id: int) -> bool:
    settings = get_settings()
    raw = (settings.discord_filter_admin_ids or "").strip()
    if not raw:
        # fallback : même user que les tests courants du propriétaire si non configuré
        return False
    allowed = {p.strip() for p in raw.replace(";", ",").split(",") if p.strip()}
    return str(user_id) in allowed


def _sync_resello_dm(
    discord_user_id: int,
    *,
    discord_username: str | None = None,
) -> tuple[bool, str | None]:
    from vinted_bot.services.resello_dm import sync_resello_filters_dm

    return sync_resello_filters_dm(
        discord_user_id=discord_user_id,
        discord_username=discord_username,
    )


def _dm_sync_extra(dm_ok: bool, dm_err: str | None) -> str:
    if dm_ok:
        return (
            "\n\n📬 **DM Resello** mis à jour "
            "(consultation + activer/désactiver).\n"
            "Création / modification / suppression → salon **MES ALERTES**."
        )
    return (
        "\n\n⚠️ **Impossible d’ouvrir tes DM** — autorise les MP du serveur "
        "(Confidentialité Discord), sinon tu ne recevras pas les alertes.\n"
        f"_Erreur : `{dm_err}`_"
    )


def _resello_dm_payload_for(
    discord_user_id: int,
    *,
    discord_username: str | None = None,
) -> dict[str, Any]:
    from types import SimpleNamespace

    from vinted_bot.db.user_filters import (
        get_or_create_member_plan,
        list_user_filters,
        plan_filter_limit,
    )
    from vinted_bot.services.resello_dm import build_resello_dm_dashboard_payload

    with session_scope() as session:
        plan_row = get_or_create_member_plan(
            session,
            discord_user_id,
            discord_username=discord_username,
        )
        filters = list_user_filters(session, discord_user_id)
        snapshot = [
            SimpleNamespace(
                id=int(f.id),
                name=f.name,
                brand=f.brand,
                model=f.model,
                category=f.category,
                keyword=f.keyword,
                min_price_eur=f.min_price_eur,
                max_price_eur=f.max_price_eur,
                is_active=bool(f.is_active),
            )
            for f in filters
        ]
        plan = plan_row.plan
        limit = plan_filter_limit(plan)
    return build_resello_dm_dashboard_payload(
        plan=plan, filters=snapshot, limit=limit
    )


def _user_alerts_payload_for(
    discord_user_id: int,
    *,
    discord_username: str | None = None,
) -> dict[str, Any]:
    from types import SimpleNamespace

    from vinted_bot.db.user_filters import (
        get_or_create_member_plan,
        list_user_filters,
        plan_filter_limit,
    )

    with session_scope() as session:
        plan_row = get_or_create_member_plan(
            session,
            discord_user_id,
            discord_username=discord_username,
        )
        filters = list_user_filters(session, discord_user_id)
        snapshot = [
            SimpleNamespace(
                id=int(f.id),
                name=f.name,
                brand=f.brand,
                model=f.model,
                category=f.category,
                keyword=f.keyword,
                min_price_eur=f.min_price_eur,
                max_price_eur=f.max_price_eur,
                is_active=bool(f.is_active),
            )
            for f in filters
        ]
        plan = plan_row.plan
        limit = plan_filter_limit(plan)
    return build_user_alerts_payload(plan=plan, filters=snapshot, limit=limit)


def handle_filtres_command(
    client: DiscordInteractionClient,
    interaction: dict[str, Any],
) -> None:
    user = _interaction_user(interaction)
    discord_user_id = int(user["id"])
    payload = _user_alerts_payload_for(
        discord_user_id,
        discord_username=_discord_display_name(user),
    )
    client.respond_ephemeral_payload(
        interaction["id"],
        interaction["token"],
        payload,
    )


def handle_filtre_creer_command(
    client: DiscordInteractionClient,
    interaction: dict[str, Any],
) -> None:
    user = _interaction_user(interaction)
    discord_user_id = int(user["id"])
    opts = _option_map(interaction)
    brand = opts.get("marque")
    model = opts.get("modele")
    category = opts.get("categorie")
    keyword = opts.get("mot_cle")
    nom = opts.get("nom")
    prix_max = opts.get("prix_max")
    prix_min = opts.get("prix_min")

    if not any([brand, model, category, keyword, prix_max is not None, prix_min is not None]):
        client.respond_ephemeral(
            interaction["id"],
            interaction["token"],
            "❌ Indique au moins un critère (marque, modèle, catégorie, mot-clé ou prix).",
        )
        return

    from vinted_bot.db.user_filters import (
        count_user_filters,
        create_user_filter,
        filter_display_number,
        filter_has_criteria,
        get_or_create_member_plan,
        plan_filter_limit,
        summarize_filter,
    )

    with session_scope() as session:
        plan_row = get_or_create_member_plan(
            session,
            discord_user_id,
            discord_username=_discord_display_name(user),
        )
        limit = plan_filter_limit(plan_row.plan)
        current = count_user_filters(session, discord_user_id)
        if limit is not None and current >= limit:
            client.respond_ephemeral(
                interaction["id"],
                interaction["token"],
                (
                    f"❌ Limite atteinte ({current}/{limit}) pour le plan `{plan_row.plan}`.\n"
                    "Passe Premium/Elite ou `/filtre-supprimer` un filtre."
                ),
            )
            return
        row = create_user_filter(
            session,
            discord_user_id=discord_user_id,
            brand=str(brand) if brand else None,
            model=str(model) if model else None,
            category=str(category) if category else None,
            keyword=str(keyword) if keyword else None,
            max_price_eur=float(prix_max) if prix_max is not None else None,
            min_price_eur=float(prix_min) if prix_min is not None else None,
            name=str(nom) if nom else None,
        )
        if not filter_has_criteria(row):
            session.delete(row)
            session.flush()
            client.respond_ephemeral(
                interaction["id"],
                interaction["token"],
                "❌ Filtre invalide.",
            )
            return
        filter_id = int(row.id)
        display_n = filter_display_number(
            session, discord_user_id=discord_user_id, filter_id=filter_id
        )
        summary = summarize_filter(row, display_number=display_n)

    dm_ok, dm_err = _sync_resello_dm(
        discord_user_id,
        discord_username=_discord_display_name(user),
    )
    client.respond_ephemeral(
        interaction["id"],
        interaction["token"],
        (f"✅ **Alerte privée créée**\n{summary}{_dm_sync_extra(dm_ok, dm_err)}")[:2000],
    )


def handle_filtre_supprimer_command(
    client: DiscordInteractionClient,
    interaction: dict[str, Any],
) -> None:
    user = _interaction_user(interaction)
    discord_user_id = int(user["id"])
    opts = _option_map(interaction)
    filter_id = int(opts.get("id") or 0)
    from vinted_bot.db.user_filters import delete_user_filter, get_user_filter

    with session_scope() as session:
        row = get_user_filter(
            session, filter_id=filter_id, discord_user_id=discord_user_id
        )
        if row is None:
            client.respond_ephemeral(
                interaction["id"],
                interaction["token"],
                f"❌ Filtre `#{filter_id}` introuvable (ou pas à toi).",
            )
            return
        delete_user_filter(session, row)
    _sync_resello_dm(
        discord_user_id,
        discord_username=_discord_display_name(user),
    )
    client.respond_ephemeral(
        interaction["id"],
        interaction["token"],
        f"🗑️ Filtre `#{filter_id}` supprimé.",
    )


def handle_filtre_toggle_command(
    client: DiscordInteractionClient,
    interaction: dict[str, Any],
) -> None:
    user = _interaction_user(interaction)
    discord_user_id = int(user["id"])
    opts = _option_map(interaction)
    filter_id = int(opts.get("id") or 0)
    from vinted_bot.db.user_filters import (
        filter_display_number,
        get_user_filter,
        summarize_filter,
        toggle_user_filter,
    )

    with session_scope() as session:
        row = get_user_filter(
            session, filter_id=filter_id, discord_user_id=discord_user_id
        )
        if row is None:
            client.respond_ephemeral(
                interaction["id"],
                interaction["token"],
                f"❌ Filtre `#{filter_id}` introuvable (ou pas à toi).",
            )
            return
        toggle_user_filter(session, row)
        display_n = filter_display_number(
            session, discord_user_id=discord_user_id, filter_id=filter_id
        )
        summary = summarize_filter(row, display_number=display_n)
    _sync_resello_dm(
        discord_user_id,
        discord_username=_discord_display_name(user),
    )
    client.respond_ephemeral(
        interaction["id"],
        interaction["token"],
        f"🔄 {summary}",
    )


def handle_filtre_plan_command(
    client: DiscordInteractionClient,
    interaction: dict[str, Any],
) -> None:
    user = _interaction_user(interaction)
    discord_user_id = int(user["id"])
    from vinted_bot.db.user_filters import (
        count_user_filters,
        get_or_create_member_plan,
        plan_filter_limit,
    )

    with session_scope() as session:
        plan_row = get_or_create_member_plan(
            session,
            discord_user_id,
            discord_username=_discord_display_name(user),
        )
        n = count_user_filters(session, discord_user_id)
        plan = plan_row.plan
        limit = plan_filter_limit(plan)
    limit_txt = "illimité" if limit is None else str(limit)
    client.respond_ephemeral(
        interaction["id"],
        interaction["token"],
        (
            f"📦 **Ton plan filtres privés**\n"
            f"Plan : `{plan}`\n"
            f"Filtres : **{n}** / **{limit_txt}**\n\n"
            f"Starter=5 · Premium=20 · Elite=∞\n"
            f"_Les alertes partent en DM, jamais en salon public._"
        ),
    )


def handle_set_plan_command(
    client: DiscordInteractionClient,
    interaction: dict[str, Any],
) -> None:
    user = _interaction_user(interaction)
    admin_id = int(user["id"])
    if not _is_filter_admin(admin_id):
        client.respond_ephemeral(
            interaction["id"],
            interaction["token"],
            "❌ Réservé aux admins (`DISCORD_FILTER_ADMIN_IDS`).",
        )
        return
    opts = _option_map(interaction)
    target_id = int(opts.get("user") or 0)
    plan = str(opts.get("plan") or "starter")
    from vinted_bot.db.user_filters import set_member_plan

    with session_scope() as session:
        row = set_member_plan(session, target_id, plan)
    client.respond_ephemeral(
        interaction["id"],
        interaction["token"],
        f"✅ Plan de <@{target_id}> → `{row.plan}`",
    )


def _modal_values(interaction: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for row in interaction.get("data", {}).get("components") or []:
        if not isinstance(row, dict):
            continue
        for comp in row.get("components") or []:
            if isinstance(comp, dict) and comp.get("custom_id"):
                out[str(comp["custom_id"])] = str(comp.get("value") or "").strip()
    return out


def handle_alert_create_button(
    client: DiscordInteractionClient,
    interaction: dict[str, Any],
) -> None:
    client.respond_modal(
        interaction["id"],
        interaction["token"],
        build_create_alert_modal(),
    )


def handle_alert_list_button(
    client: DiscordInteractionClient,
    interaction: dict[str, Any],
) -> None:
    handle_filtres_command(client, interaction)


def _selected_filter_id(interaction: dict[str, Any]) -> int | None:
    values = interaction.get("data", {}).get("values") or []
    if not values:
        return None
    try:
        return int(values[0])
    except (TypeError, ValueError):
        return None


def handle_alert_edit_select(
    client: DiscordInteractionClient,
    interaction: dict[str, Any],
) -> None:
    user = _interaction_user(interaction)
    discord_user_id = int(user["id"])
    filter_id = _selected_filter_id(interaction)
    if filter_id is None:
        client.respond_ephemeral(
            interaction["id"],
            interaction["token"],
            "❌ Filtre invalide.",
        )
        return
    from vinted_bot.db.user_filters import filter_display_number, get_user_filter

    with session_scope() as session:
        row = get_user_filter(
            session, filter_id=filter_id, discord_user_id=discord_user_id
        )
        if row is None:
            client.respond_ephemeral(
                interaction["id"],
                interaction["token"],
                f"❌ Filtre `#{filter_id}` introuvable.",
            )
            return
        display_n = filter_display_number(
            session, discord_user_id=discord_user_id, filter_id=filter_id
        )
        modal = build_edit_alert_modal(
            filter_id=filter_id, row=row, display_number=display_n
        )
    client.respond_modal(interaction["id"], interaction["token"], modal)


def handle_alert_pause_select(
    client: DiscordInteractionClient,
    interaction: dict[str, Any],
) -> None:
    user = _interaction_user(interaction)
    discord_user_id = int(user["id"])
    filter_id = _selected_filter_id(interaction)
    if filter_id is None:
        client.respond_ephemeral(
            interaction["id"],
            interaction["token"],
            "❌ Filtre invalide.",
        )
        return
    from vinted_bot.db.user_filters import get_user_filter, toggle_user_filter

    with session_scope() as session:
        row = get_user_filter(
            session, filter_id=filter_id, discord_user_id=discord_user_id
        )
        if row is None:
            client.respond_ephemeral(
                interaction["id"],
                interaction["token"],
                f"❌ Filtre `#{filter_id}` introuvable.",
            )
            return
        toggle_user_filter(session, row)
    payload = _user_alerts_payload_for(
        discord_user_id,
        discord_username=_discord_display_name(user),
    )
    client.respond_update_message(
        interaction["id"],
        interaction["token"],
        payload,
    )
    _sync_resello_dm(
        discord_user_id,
        discord_username=_discord_display_name(user),
    )


def handle_alert_delete_select(
    client: DiscordInteractionClient,
    interaction: dict[str, Any],
) -> None:
    user = _interaction_user(interaction)
    discord_user_id = int(user["id"])
    filter_id = _selected_filter_id(interaction)
    if filter_id is None:
        client.respond_ephemeral(
            interaction["id"],
            interaction["token"],
            "❌ Filtre invalide.",
        )
        return
    from vinted_bot.db.user_filters import delete_user_filter, get_user_filter

    with session_scope() as session:
        row = get_user_filter(
            session, filter_id=filter_id, discord_user_id=discord_user_id
        )
        if row is None:
            client.respond_ephemeral(
                interaction["id"],
                interaction["token"],
                f"❌ Filtre `#{filter_id}` introuvable.",
            )
            return
        delete_user_filter(session, row)
    payload = _user_alerts_payload_for(
        discord_user_id,
        discord_username=_discord_display_name(user),
    )
    client.respond_update_message(
        interaction["id"],
        interaction["token"],
        payload,
    )
    _sync_resello_dm(
        discord_user_id,
        discord_username=_discord_display_name(user),
    )


def handle_alert_dm_toggle(
    client: DiscordInteractionClient,
    interaction: dict[str, Any],
) -> None:
    """Bouton DM Resello : activer / désactiver uniquement."""
    user = _interaction_user(interaction)
    discord_user_id = int(user["id"])
    custom_id = str(interaction.get("data", {}).get("custom_id", ""))
    from vinted_bot.services.resello_dm import parse_dm_toggle_filter_id

    filter_id = parse_dm_toggle_filter_id(custom_id)
    if filter_id is None:
        client.respond_ephemeral(
            interaction["id"],
            interaction["token"],
            "❌ Filtre invalide.",
        )
        return
    from vinted_bot.db.user_filters import get_user_filter, toggle_user_filter

    with session_scope() as session:
        row = get_user_filter(
            session, filter_id=filter_id, discord_user_id=discord_user_id
        )
        if row is None:
            client.respond_ephemeral(
                interaction["id"],
                interaction["token"],
                "❌ Filtre introuvable.",
            )
            return
        toggle_user_filter(session, row)

    payload = _resello_dm_payload_for(
        discord_user_id,
        discord_username=_discord_display_name(user),
    )
    client.respond_update_message(
        interaction["id"],
        interaction["token"],
        payload,
    )


def handle_alert_create_modal(
    client: DiscordInteractionClient,
    interaction: dict[str, Any],
) -> None:
    user = _interaction_user(interaction)
    discord_user_id = int(user["id"])
    values = _modal_values(interaction)
    brand = values.get("marque") or None
    model = values.get("modele") or None
    category = values.get("categorie") or None
    keyword = values.get("mot_cle") or None
    prix_raw = values.get("prix_max") or ""
    prix_max: float | None = None
    if prix_raw:
        try:
            prix_max = float(prix_raw.replace(",", ".").replace("€", "").strip())
        except ValueError:
            client.respond_ephemeral(
                interaction["id"],
                interaction["token"],
                "❌ Prix max invalide — utilise un nombre (ex. 50).",
            )
            return

    if not any([brand, model, category, keyword, prix_max is not None]):
        client.respond_ephemeral(
            interaction["id"],
            interaction["token"],
            "❌ Indique au moins un critère (marque, modèle, catégorie, mot-clé ou prix).",
        )
        return

    from vinted_bot.db.user_filters import (
        count_user_filters,
        create_user_filter,
        filter_display_number,
        filter_has_criteria,
        get_or_create_member_plan,
        plan_filter_limit,
        summarize_filter,
    )

    with session_scope() as session:
        plan_row = get_or_create_member_plan(
            session,
            discord_user_id,
            discord_username=_discord_display_name(user),
        )
        limit = plan_filter_limit(plan_row.plan)
        current = count_user_filters(session, discord_user_id)
        if limit is not None and current >= limit:
            client.respond_ephemeral(
                interaction["id"],
                interaction["token"],
                (
                    f"❌ Limite atteinte ({current}/{limit}) pour le plan `{plan_row.plan}`.\n"
                    "Supprime une alerte ou upgrade ton plan."
                ),
            )
            return
        row = create_user_filter(
            session,
            discord_user_id=discord_user_id,
            brand=brand,
            model=model,
            category=category,
            keyword=keyword,
            max_price_eur=prix_max,
        )
        if not filter_has_criteria(row):
            session.delete(row)
            session.flush()
            client.respond_ephemeral(
                interaction["id"],
                interaction["token"],
                "❌ Alerte invalide.",
            )
            return
        filter_id = int(row.id)
        display_n = filter_display_number(
            session, discord_user_id=discord_user_id, filter_id=filter_id
        )
        summary = summarize_filter(row, display_number=display_n)

    # DM dashboard unique (pas de backfill d'anciennes annonces)
    dm_ok, dm_err = _sync_resello_dm(
        discord_user_id,
        discord_username=_discord_display_name(user),
    )

    client.respond_ephemeral(
        interaction["id"],
        interaction["token"],
        (f"✅ **Alerte privée créée**\n{summary}{_dm_sync_extra(dm_ok, dm_err)}")[:2000],
    )


def handle_alert_edit_modal(
    client: DiscordInteractionClient,
    interaction: dict[str, Any],
) -> None:
    user = _interaction_user(interaction)
    discord_user_id = int(user["id"])
    custom_id = str(interaction.get("data", {}).get("custom_id", ""))
    if not custom_id.startswith(ALERT_EDIT_MODAL_PREFIX):
        client.respond_ephemeral(
            interaction["id"],
            interaction["token"],
            "❌ Modal invalide.",
        )
        return
    try:
        filter_id = int(custom_id[len(ALERT_EDIT_MODAL_PREFIX) :])
    except ValueError:
        client.respond_ephemeral(
            interaction["id"],
            interaction["token"],
            "❌ Filtre invalide.",
        )
        return

    values = _modal_values(interaction)
    brand = values.get("marque") or ""
    model = values.get("modele") or ""
    category = values.get("categorie") or ""
    keyword = values.get("mot_cle") or ""
    prix_raw = values.get("prix_max") or ""
    prix_max: float | None = None
    clear_max = not bool(prix_raw.strip())
    if prix_raw.strip():
        try:
            prix_max = float(prix_raw.replace(",", ".").replace("€", "").strip())
        except ValueError:
            client.respond_ephemeral(
                interaction["id"],
                interaction["token"],
                "❌ Prix max invalide — utilise un nombre (ex. 50).",
            )
            return

    if not any([brand.strip(), model.strip(), category.strip(), keyword.strip(), prix_max is not None]):
        client.respond_ephemeral(
            interaction["id"],
            interaction["token"],
            "❌ Indique au moins un critère.",
        )
        return

    from vinted_bot.db.user_filters import (
        filter_display_number,
        filter_has_criteria,
        get_user_filter,
        summarize_filter,
        update_user_filter,
    )

    with session_scope() as session:
        row = get_user_filter(
            session, filter_id=filter_id, discord_user_id=discord_user_id
        )
        if row is None:
            client.respond_ephemeral(
                interaction["id"],
                interaction["token"],
                f"❌ Filtre `#{filter_id}` introuvable.",
            )
            return
        update_user_filter(
            session,
            row,
            brand=brand,
            model=model,
            category=category,
            keyword=keyword,
            max_price_eur=prix_max,
            clear_max_price=clear_max,
        )
        if not filter_has_criteria(row):
            client.respond_ephemeral(
                interaction["id"],
                interaction["token"],
                "❌ Alerte invalide après modification.",
            )
            return
        display_n = filter_display_number(
            session, discord_user_id=discord_user_id, filter_id=filter_id
        )
        summary = summarize_filter(row, display_number=display_n)

    _sync_resello_dm(
        discord_user_id,
        discord_username=_discord_display_name(user),
    )
    client.respond_ephemeral(
        interaction["id"],
        interaction["token"],
        f"✅ Filtre mis à jour\n{summary}",
    )


def handle_reglement_accept(
    client: DiscordInteractionClient,
    interaction: dict[str, Any],
) -> None:
    user = _interaction_user(interaction)
    user_id = int(user.get("id") or 0)
    if not user_id:
        client.respond_ephemeral(
            interaction["id"],
            interaction["token"],
            "Impossible d'identifier ton compte Discord.",
        )
        return

    guild_id = str(interaction.get("guild_id") or "").strip()
    member = interaction.get("member") or {}
    member_roles = {str(r) for r in (member.get("roles") or [])}
    role_id = client.reglement_verified_role_id()

    if role_id and role_id in member_roles:
        client.respond_ephemeral(
            interaction["id"],
            interaction["token"],
            "✅ Tu as déjà accepté le règlement — accès confirmé.",
        )
        return

    if role_id:
        try:
            client.add_guild_member_role(guild_id, user_id, role_id)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "reglement_role_failed",
                user_id=user_id,
                role_id=role_id,
                error=str(exc)[:200],
            )
            client.respond_ephemeral(
                interaction["id"],
                interaction["token"],
                (
                    "❌ Impossible d'attribuer le rôle pour le moment.\n"
                    "Contacte un admin — le bot doit avoir **Gérer les rôles** "
                    "et le rôle doit être **sous** le rôle du bot."
                ),
            )
            return
        client.respond_ephemeral(
            interaction["id"],
            interaction["token"],
            (
                "✅ **Règlement accepté** — bienvenue sur Resello !\n\n"
                "Tu peux maintenant accéder aux salons du serveur."
            ),
        )
        log.info("reglement_accepted", user_id=user_id, role_id=role_id)
        return

    client.respond_ephemeral(
        interaction["id"],
        interaction["token"],
        (
            "✅ **Règlement accepté** — merci d'avoir pris connaissance "
            "des règles du serveur."
        ),
    )
    log.info("reglement_accepted_no_role", user_id=user_id)


def dispatch_interaction(
    client: DiscordInteractionClient,
    interaction: dict[str, Any],
    *,
    already_deferred: bool = False,
) -> None:
    interaction_type = interaction.get("type")
    if interaction_type == 2:
        name = interaction.get("data", {}).get("name")
        if name == "filtres":
            handle_filtres_command(client, interaction)
        elif name == "filtre-creer":
            handle_filtre_creer_command(client, interaction)
        elif name == "filtre-supprimer":
            handle_filtre_supprimer_command(client, interaction)
        elif name == "filtre-toggle":
            handle_filtre_toggle_command(client, interaction)
        elif name == "filtre-plan":
            handle_filtre_plan_command(client, interaction)
        elif name == "set-plan":
            handle_set_plan_command(client, interaction)
        else:
            client.respond_ephemeral(
                interaction["id"],
                interaction["token"],
                "Commande inconnue.",
            )
        return

    if interaction_type == 3:
        custom_id = str(interaction.get("data", {}).get("custom_id", ""))
        if custom_id == ALERT_CREATE:
            handle_alert_create_button(client, interaction)
            return
        if custom_id == ALERT_LIST:
            handle_alert_list_button(client, interaction)
            return
        if custom_id == ALERT_EDIT_SELECT:
            handle_alert_edit_select(client, interaction)
            return
        if custom_id == ALERT_PAUSE_SELECT:
            handle_alert_pause_select(client, interaction)
            return
        if custom_id == ALERT_DELETE_SELECT:
            handle_alert_delete_select(client, interaction)
            return
        if custom_id.startswith("alert:dm_toggle:"):
            handle_alert_dm_toggle(client, interaction)
            return
        if custom_id == REGLEMENT_ACCEPT:
            handle_reglement_accept(client, interaction)
            return

    if interaction_type == 5:
        custom_id = str(interaction.get("data", {}).get("custom_id", ""))
        if custom_id == ALERT_CREATE_MODAL:
            handle_alert_create_modal(client, interaction)
            return
        if custom_id.startswith(ALERT_EDIT_MODAL_PREFIX):
            handle_alert_edit_modal(client, interaction)
            return

    client.respond_ephemeral(
        interaction["id"],
        interaction["token"],
        "Interaction non gérée.",
    )

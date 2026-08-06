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
from vinted_bot.interactions.whop_claim_panel import (
    WHOP_CHECKOUT_PREFIX,
    WHOP_CLAIM,
    WHOP_CLAIM_FIELD,
    WHOP_CLAIM_MODAL,
    build_whop_claim_modal,
)
from vinted_bot.interactions.recruitment_panel import (
    RECRUIT_CLOSE,
    RECRUIT_OPEN,
    build_ticket_candidature_payload,
    build_ticket_overwrites,
    find_open_ticket_channel,
    format_ticket_transcript,
    parse_ticket_opener_id,
    sanitize_ticket_channel_name,
    ticket_topic_for_user,
)
from vinted_bot.interactions.support_panel import (
    SUPPORT_CLOSE,
    SUPPORT_OPEN,
    build_support_ticket_payload,
    find_open_support_ticket,
    parse_ticket_opener_id as parse_support_opener_id,
    sanitize_support_channel_name,
    ticket_topic_for_user as support_ticket_topic_for_user,
)
from vinted_bot.interactions.discord_api import DiscordInteractionClient
from vinted_bot.utils.logging import get_logger

log = get_logger(__name__)


def _member_role_ids(interaction: dict[str, Any]) -> set[str]:
    member = interaction.get("member") or {}
    return {str(r) for r in (member.get("roles") or [])}


def _has_resello_vip(interaction: dict[str, Any]) -> bool:
    """True si le membre a un rôle d'abonnement (starter/pro/pro+ ou VIP legacy)."""
    from vinted_bot.services.whop_webhooks import all_subscription_role_ids

    roles = _member_role_ids(interaction)
    return any(rid in roles for rid in all_subscription_role_ids())


def _plan_from_discord_roles(interaction: dict[str, Any]) -> str | None:
    """Infère le plan depuis les rôles Discord (Pro+ > Pro > Starter)."""
    from vinted_bot.config import get_settings

    settings = get_settings()
    roles = _member_role_ids(interaction)
    proplus = (settings.discord_role_sub_proplus or "").strip()
    pro = (settings.discord_role_sub_pro or "").strip()
    starter = (settings.discord_role_sub_starter or "").strip()
    vip = (settings.discord_role_resello_vip or "").strip()
    # VIP legacy = même chose que Pro si pas de rôle Pro+ distinct
    if proplus and proplus in roles:
        return "elite"
    if pro and pro in roles:
        return "premium"
    if vip and vip in roles:
        # Même ID que Pro → Pro ; sinon abonné générique = Pro (10 filtres)
        return "premium"
    if starter and starter in roles:
        return "starter"
    return None


def _align_db_plan_with_roles(
    interaction: dict[str, Any],
    discord_user_id: int,
    *,
    discord_username: str | None = None,
) -> tuple[str, int | None, bool]:
    """Retourne (plan, limit, subscription_active) et sync DB si le rôle Pro/Pro+ est là."""
    from vinted_bot.db.user_filters import (
        get_or_create_member_plan,
        plan_filter_limit,
        set_member_plan,
    )

    from_roles = _plan_from_discord_roles(interaction)
    with session_scope() as session:
        plan_row = get_or_create_member_plan(
            session,
            discord_user_id,
            discord_username=discord_username,
        )
        db_plan = plan_row.plan
        active = bool(getattr(plan_row, "subscription_active", False))
        if from_roles and from_roles != "starter":
            if db_plan != from_roles or not active:
                plan_row = set_member_plan(
                    session,
                    discord_user_id,
                    from_roles,
                    discord_username=discord_username,
                    subscription_active=True,
                )
                active = True
            plan = from_roles
        elif from_roles == "starter":
            if not active:
                plan_row = set_member_plan(
                    session,
                    discord_user_id,
                    "starter",
                    discord_username=discord_username,
                    subscription_active=True,
                )
                active = True
            plan = "starter"
        else:
            plan = db_plan
        limit = plan_filter_limit(plan)
    return plan, limit, active


def _private_filters_gate_message(
    *,
    plan: str,
    limit: int | None,
    subscription_active: bool,
    has_vip: bool,
) -> str | None:
    """None = OK pour créer un filtre ; sinon message d'erreur."""
    from vinted_bot.db.user_filters import normalize_plan

    plan_n = normalize_plan(plan)
    # Abonnement Whop (rôle Pro / VIP ou flag DB) requis
    if not subscription_active and not has_vip:
        return (
            "❌ Les filtres privés sont réservés aux abonnés Resello.\n"
            "Prends un abonnement dans **Nos offres** (Whop)."
        )
    if limit is not None and limit <= 0:
        return (
            "❌ Ton abonnement **Starter** n'inclut pas les filtres privés.\n"
            "Passe **Pro** (10) ou **Pro+** (30) dans Nos offres."
        )
    if plan_n == "starter" and (limit is None or limit <= 0):
        return (
            "❌ Ton abonnement **Starter** n'inclut pas les filtres privés.\n"
            "Passe **Pro** ou **Pro+** dans Nos offres."
        )
    return None


def _interaction_user(interaction: dict[str, Any]) -> dict[str, Any]:
    member = interaction.get("member") or {}
    user = member.get("user") or interaction.get("user") or {}
    return user if isinstance(user, dict) else {}


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
    interaction: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from types import SimpleNamespace

    from vinted_bot.db.user_filters import list_user_filters

    if interaction is not None:
        plan, limit, _active = _align_db_plan_with_roles(
            interaction,
            discord_user_id,
            discord_username=discord_username,
        )
    else:
        from vinted_bot.db.user_filters import (
            get_or_create_member_plan,
            plan_filter_limit,
        )

        with session_scope() as session:
            plan_row = get_or_create_member_plan(
                session,
                discord_user_id,
                discord_username=discord_username,
            )
            plan = plan_row.plan
            limit = plan_filter_limit(plan)

    with session_scope() as session:
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
        interaction=interaction,
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
        summarize_filter,
    )

    plan, limit, active = _align_db_plan_with_roles(
        interaction,
        discord_user_id,
        discord_username=_discord_display_name(user),
    )
    gate = _private_filters_gate_message(
        plan=plan,
        limit=limit,
        subscription_active=active,
        has_vip=_has_resello_vip(interaction),
    )
    if gate:
        client.respond_ephemeral(
            interaction["id"],
            interaction["token"],
            gate,
        )
        return

    with session_scope() as session:
        current = count_user_filters(session, discord_user_id)
        if limit is not None and current >= limit:
            client.respond_ephemeral(
                interaction["id"],
                interaction["token"],
                (
                    f"❌ Limite atteinte ({current}/{limit}) pour le plan `{plan}`.\n"
                    "Passe Pro/Pro+ ou `/filtre-supprimer` un filtre."
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
    from vinted_bot.db.user_filters import count_user_filters

    plan, limit, active = _align_db_plan_with_roles(
        interaction,
        discord_user_id,
        discord_username=_discord_display_name(user),
    )
    with session_scope() as session:
        n = count_user_filters(session, discord_user_id)
    limit_txt = "illimité" if limit is None else str(limit)
    client.respond_ephemeral(
        interaction["id"],
        interaction["token"],
        (
            f"📦 **Ton plan filtres privés**\n"
            f"Plan : `{plan}`\n"
            f"Abonnement actif : **{'oui' if active else 'non'}**\n"
            f"Filtres : **{n}** / **{limit_txt}**\n\n"
            f"Starter=0 · Pro=10 · Pro+=30\n"
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
    from vinted_bot.db.user_filters import (
        deactivate_all_user_filters,
        normalize_plan,
        plan_filter_limit,
        set_member_plan,
    )
    from vinted_bot.services.whop_webhooks import sync_subscription_roles

    plan_n = normalize_plan(plan)
    with session_scope() as session:
        row = set_member_plan(
            session,
            target_id,
            plan_n,
            subscription_active=True,
        )
        paused = 0
        limit = plan_filter_limit(plan_n)
        if limit is not None and limit <= 0:
            paused = deactivate_all_user_filters(session, target_id)
    try:
        sync_subscription_roles(
            discord_user_id=target_id,
            plan=plan_n,
            active=True,
        )
        vip_txt = f"rôles sync plan `{plan_n}`"
    except Exception as exc:  # noqa: BLE001
        vip_txt = f"rôles non sync ({str(exc)[:80]})"
    extra = f" · {paused} filtre(s) en pause" if paused else ""
    client.respond_ephemeral(
        interaction["id"],
        interaction["token"],
        f"✅ Plan de <@{target_id}> → `{row.plan}` ({vip_txt}){extra}",
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
    user = _interaction_user(interaction)
    discord_user_id = int(user["id"])
    plan, limit, active = _align_db_plan_with_roles(
        interaction,
        discord_user_id,
        discord_username=_discord_display_name(user),
    )
    gate = _private_filters_gate_message(
        plan=plan,
        limit=limit,
        subscription_active=active,
        has_vip=_has_resello_vip(interaction),
    )
    if gate:
        client.respond_ephemeral(
            interaction["id"],
            interaction["token"],
            gate,
        )
        return
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
        interaction=interaction,
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
        interaction=interaction,
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
        summarize_filter,
    )

    plan, limit, active = _align_db_plan_with_roles(
        interaction,
        discord_user_id,
        discord_username=_discord_display_name(user),
    )
    gate = _private_filters_gate_message(
        plan=plan,
        limit=limit,
        subscription_active=active,
        has_vip=_has_resello_vip(interaction),
    )
    if gate:
        client.respond_ephemeral(
            interaction["id"],
            interaction["token"],
            gate,
        )
        return

    with session_scope() as session:
        current = count_user_filters(session, discord_user_id)
        if limit is not None and current >= limit:
            client.respond_ephemeral(
                interaction["id"],
                interaction["token"],
                (
                    f"❌ Limite atteinte ({current}/{limit}) pour le plan `{plan}`.\n"
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


def _reglement_reply(
    client: DiscordInteractionClient,
    interaction: dict[str, Any],
    content: str,
    *,
    already_deferred: bool,
) -> None:
    if already_deferred:
        client.edit_original(interaction["token"], content=content)
    else:
        client.respond_ephemeral(
            interaction["id"],
            interaction["token"],
            content,
        )


def handle_whop_claim_button(
    client: DiscordInteractionClient,
    interaction: dict[str, Any],
) -> None:
    client.respond_modal(
        interaction["id"],
        interaction["token"],
        build_whop_claim_modal(),
    )


def handle_whop_claim_modal(
    client: DiscordInteractionClient,
    interaction: dict[str, Any],
) -> None:
    from vinted_bot.services.whop_webhooks import claim_whop_access

    user = _interaction_user(interaction)
    user_id = int(user.get("id") or 0)
    if not user_id:
        client.respond_ephemeral(
            interaction["id"],
            interaction["token"],
            "Impossible d'identifier ton compte Discord.",
        )
        return
    values = _modal_values(interaction)
    reference = (values.get(WHOP_CLAIM_FIELD) or "").strip()
    ok, message = claim_whop_access(
        discord_user_id=user_id,
        reference=reference,
        discord_username=_discord_display_name(user),
    )
    prefix = "✅ " if ok else "❌ "
    client.respond_ephemeral(
        interaction["id"],
        interaction["token"],
        f"{prefix}{message}",
    )


def handle_whop_checkout_button(
    client: DiscordInteractionClient,
    interaction: dict[str, Any],
) -> None:
    from vinted_bot.services.whop_webhooks import create_checkout_url_for_discord

    custom_id = str(interaction.get("data", {}).get("custom_id", ""))
    tier = custom_id[len(WHOP_CHECKOUT_PREFIX) :].strip().lower()
    user = _interaction_user(interaction)
    user_id = int(user.get("id") or 0)
    if not user_id:
        client.respond_ephemeral(
            interaction["id"],
            interaction["token"],
            "Impossible d'identifier ton compte Discord.",
        )
        return
    url, err = create_checkout_url_for_discord(
        tier=tier,
        discord_user_id=user_id,
    )
    if not url:
        client.respond_ephemeral(
            interaction["id"],
            interaction["token"],
            "Lien checkout indisponible pour cette offre. "
            "Réessaie dans 1 min ou contacte le support.",
        )
        return
    if err:
        # Fallback lien statique : rôle auto pas garanti
        client.respond_ephemeral(
            interaction["id"],
            interaction["token"],
            f"🔗 Lien **{tier.upper()}** :\n{url}\n\n"
            "⚠️ Lien standard (auto-rôle non garanti). "
            "Après paiement → **Activer mon accès**.",
        )
        return
    client.respond_ephemeral(
        interaction["id"],
        interaction["token"],
        f"🔗 Ton lien **{tier.upper()}** (lié à ton Discord) :\n{url}\n\n"
        "Paie avec ce lien → le rôle Resello est mis **automatiquement**.",
    )


def handle_reglement_accept(
    client: DiscordInteractionClient,
    interaction: dict[str, Any],
    *,
    already_deferred: bool = False,
) -> None:
    user = _interaction_user(interaction)
    user_id = int(user.get("id") or 0)
    if not user_id:
        _reglement_reply(
            client,
            interaction,
            "Impossible d'identifier ton compte Discord.",
            already_deferred=already_deferred,
        )
        return

    guild_id = str(interaction.get("guild_id") or "").strip()
    if not guild_id:
        _reglement_reply(
            client,
            interaction,
            "Impossible d'identifier le serveur Discord.",
            already_deferred=already_deferred,
        )
        return

    member = interaction.get("member") or {}
    member_roles = {str(r) for r in (member.get("roles") or [])}

    # Env manquant → retrouver / créer le rôle « Membre » (sinon validation sans accès)
    role_id = client.reglement_verified_role_id()
    if not role_id:
        try:
            from vinted_bot.services.reglement_gates import ensure_membre_role

            role_id = ensure_membre_role(client, guild_id)
            log.info("reglement_role_resolved", role_id=role_id, guild_id=guild_id)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "reglement_role_resolve_failed",
                guild_id=guild_id,
                error=str(exc)[:200],
            )
            _reglement_reply(
                client,
                interaction,
                (
                    "❌ Rôle **Membre** introuvable.\n"
                    "Un admin doit configurer `DISCORD_ROLE_REGLEMENT_VERIFIED` "
                    "ou lancer `setup-reglement-gates`."
                ),
                already_deferred=already_deferred,
            )
            return

    if role_id in member_roles:
        try:
            from vinted_bot.services.whop_webhooks import note_reglement_accepted

            note_reglement_accepted(user_id)
        except Exception:  # noqa: BLE001
            pass
        _reglement_reply(
            client,
            interaction,
            "✅ Tu as déjà accepté le règlement — accès confirmé.",
            already_deferred=already_deferred,
        )
        return

    if not already_deferred:
        client.defer_ephemeral(interaction["id"], interaction["token"])
        already_deferred = True

    try:
        client.add_guild_member_role(guild_id, user_id, role_id)
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "reglement_role_failed",
            user_id=user_id,
            role_id=role_id,
            error=str(exc)[:200],
        )
        _reglement_reply(
            client,
            interaction,
            (
                "❌ Impossible d'attribuer le rôle pour le moment.\n"
                "Contacte un admin — le bot doit avoir **Gérer les rôles** "
                "et le rôle **Membre** doit être **sous** le rôle du bot."
            ),
            already_deferred=already_deferred,
        )
        return

    try:
        from vinted_bot.services.whop_webhooks import note_reglement_accepted

        note_reglement_accepted(user_id)
    except Exception:  # noqa: BLE001
        pass

    _reglement_reply(
        client,
        interaction,
        (
            "✅ **Règlement accepté** — bienvenue sur Resello !\n\n"
            "Tu peux maintenant accéder aux salons du serveur."
        ),
        already_deferred=already_deferred,
    )
    log.info("reglement_accepted", user_id=user_id, role_id=role_id)


def _is_recruitment_staff(interaction: dict[str, Any], client: DiscordInteractionClient) -> bool:
    user = _interaction_user(interaction)
    user_id = int(user.get("id") or 0)
    if user_id and _is_filter_admin(user_id):
        return True
    staff_role = client.recruitment_staff_role_id()
    if not staff_role:
        return False
    member = interaction.get("member") or {}
    roles = {str(r) for r in (member.get("roles") or [])}
    return staff_role in roles


def handle_recruit_open(
    client: DiscordInteractionClient,
    interaction: dict[str, Any],
    *,
    already_deferred: bool = False,
) -> None:
    from vinted_bot.config import discord_application_id
    from vinted_bot.interactions.discord_api import sanitize_guild_id

    user = _interaction_user(interaction)
    user_id = str(user.get("id") or "").strip()
    if not user_id:
        if already_deferred:
            client.edit_original(
                interaction["token"],
                content="Impossible d'identifier ton compte Discord.",
            )
        else:
            client.respond_ephemeral(
                interaction["id"],
                interaction["token"],
                "Impossible d'identifier ton compte Discord.",
            )
        return

    guild_id = sanitize_guild_id(str(interaction.get("guild_id") or ""))
    category_id = client.recruitment_category_id()
    if not guild_id or not category_id:
        msg = (
            "❌ Recrutement non configuré.\n"
            "Renseigne `DISCORD_CATEGORY_RECRUITMENT_TICKETS` dans `.env`."
        )
        if already_deferred:
            client.edit_original(interaction["token"], content=msg)
        else:
            client.respond_ephemeral(
                interaction["id"],
                interaction["token"],
                msg,
            )
        return

    if not already_deferred:
        client.defer_ephemeral(interaction["id"], interaction["token"])

    try:
        channels = client.list_guild_channels(guild_id)
        existing = find_open_ticket_channel(
            channels,
            category_id=category_id,
            user_id=user_id,
        )
        if existing:
            cid = str(existing.get("id") or "")
            client.edit_original(
                interaction["token"],
                content=(
                    f"Tu as déjà un ticket ouvert : <#{cid}>\n"
                    "Ferme-le avant d'en ouvrir un nouveau."
                ),
            )
            return

        username = str(user.get("username") or "membre")
        channel_name = sanitize_ticket_channel_name(username)
        bot_id = discord_application_id(
            getattr(client.settings, "discord_bot_token", "") or ""
        )
        if not bot_id:
            raise RuntimeError("Impossible de déterminer l'ID du bot")

        staff_role = client.recruitment_staff_role_id()
        overwrites = build_ticket_overwrites(
            everyone_id=guild_id,
            opener_user_id=user_id,
            bot_user_id=bot_id,
            staff_role_id=staff_role,
        )
        created = client.create_guild_channel(
            guild_id,
            name=channel_name,
            parent_id=category_id,
            topic=ticket_topic_for_user(user_id),
            permission_overwrites=overwrites,
        )
        ticket_id = str(created.get("id") or "")
        if not ticket_id:
            raise RuntimeError("Salon ticket créé sans id")

        staff_mention = f"<@&{staff_role}>" if staff_role else ""
        payload = build_ticket_candidature_payload(
            opener_mention=f"<@{user_id}>",
            staff_mention=staff_mention,
        )
        content = staff_mention if staff_mention else None
        post_body: dict[str, Any] = {
            "embeds": payload["embeds"],
            "components": payload["components"],
        }
        if content:
            post_body["content"] = content
        client.post_channel_payload(ticket_id, post_body)

        client.edit_original(
            interaction["token"],
            content=(
                f"✅ Ticket créé : <#{ticket_id}>\n"
                "Réponds aux questions dans ce salon."
            ),
        )
        log.info(
            "recruit_ticket_opened",
            user_id=user_id,
            channel_id=ticket_id,
            channel_name=channel_name,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("recruit_open_failed", user_id=user_id, error=str(exc)[:240])
        client.edit_original(
            interaction["token"],
            content=(
                "❌ Impossible de créer le ticket pour le moment.\n"
                "Vérifie que le bot a **Gérer les salons** sur la catégorie "
                "et que ses overwrites n'incluent que des perms qu'il possède."
            ),
        )


def handle_recruit_close(
    client: DiscordInteractionClient,
    interaction: dict[str, Any],
    *,
    already_deferred: bool = False,
) -> None:
    user = _interaction_user(interaction)
    user_id = str(user.get("id") or "").strip()
    channel_id = str(interaction.get("channel_id") or "").strip()
    if not user_id or not channel_id:
        msg = "Impossible de fermer ce ticket."
        if already_deferred:
            client.edit_original(interaction["token"], content=msg)
        else:
            client.respond_ephemeral(
                interaction["id"],
                interaction["token"],
                msg,
            )
        return

    if not already_deferred:
        client.defer_ephemeral(interaction["id"], interaction["token"])

    try:
        channel = client.get_channel(channel_id)
        opener_id = parse_ticket_opener_id(channel.get("topic"))
        is_opener = opener_id == user_id
        is_staff = _is_recruitment_staff(interaction, client)
        if not is_opener and not is_staff:
            client.edit_original(
                interaction["token"],
                content="❌ Seul le candidat ou le staff peut fermer ce ticket.",
            )
            return

        # Collect transcript (paginate)
        collected: list[dict[str, Any]] = []
        before: str | None = None
        for _ in range(20):
            batch = client.list_channel_messages(
                channel_id, limit=100, before=before
            )
            if not batch:
                break
            collected.extend(batch)
            before = str(batch[-1].get("id") or "") or None
            if len(batch) < 100:
                break

        transcript = format_ticket_transcript(collected)
        transcript_bytes = transcript.encode("utf-8")

        dm_target = opener_id or user_id
        try:
            client.send_dm_payload(
                dm_target,
                {
                    "content": (
                        "Votre ticket a été fermé.\n"
                        "Voici un transcript du ticket."
                    ),
                },
                attachments=[("log.txt", transcript_bytes, "text/plain")],
            )
        except Exception as dm_exc:  # noqa: BLE001
            log.warning(
                "recruit_transcript_dm_failed",
                user_id=dm_target,
                error=str(dm_exc)[:200],
            )

        client.delete_channel(channel_id)
        client.edit_original(
            interaction["token"],
            content="✅ Ticket fermé. Un transcript t'a été envoyé en message privé.",
        )
        log.info(
            "recruit_ticket_closed",
            closed_by=user_id,
            opener_id=opener_id,
            channel_id=channel_id,
            messages=len(collected),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "recruit_close_failed",
            user_id=user_id,
            channel_id=channel_id,
            error=str(exc)[:240],
        )
        client.edit_original(
            interaction["token"],
            content=(
                "❌ Impossible de fermer le ticket pour le moment.\n"
                f"Détail : `{str(exc)[:120]}`"
            ),
        )


def _is_support_staff(interaction: dict[str, Any], client: DiscordInteractionClient) -> bool:
    user = _interaction_user(interaction)
    user_id = int(user.get("id") or 0)
    if user_id and _is_filter_admin(user_id):
        return True
    staff_role = client.support_staff_role_id()
    if not staff_role:
        return False
    member = interaction.get("member") or {}
    roles = {str(r) for r in (member.get("roles") or [])}
    return staff_role in roles


def handle_support_open(
    client: DiscordInteractionClient,
    interaction: dict[str, Any],
    *,
    already_deferred: bool = False,
) -> None:
    from vinted_bot.config import discord_application_id
    from vinted_bot.interactions.discord_api import sanitize_guild_id

    user = _interaction_user(interaction)
    user_id = str(user.get("id") or "").strip()
    if not user_id:
        msg = "Impossible d'identifier ton compte Discord."
        if already_deferred:
            client.edit_original(interaction["token"], content=msg)
        else:
            client.respond_ephemeral(interaction["id"], interaction["token"], msg)
        return

    guild_id = sanitize_guild_id(str(interaction.get("guild_id") or ""))
    category_id = client.support_category_id()
    if not guild_id or not category_id:
        msg = (
            "❌ Support non configuré.\n"
            "Renseigne `DISCORD_CATEGORY_RECRUITMENT_TICKETS` "
            "(ou `DISCORD_CATEGORY_SUPPORT_TICKETS`) dans `.env`."
        )
        if already_deferred:
            client.edit_original(interaction["token"], content=msg)
        else:
            client.respond_ephemeral(interaction["id"], interaction["token"], msg)
        return

    if not already_deferred:
        client.defer_ephemeral(interaction["id"], interaction["token"])

    try:
        channels = client.list_guild_channels(guild_id)
        existing = find_open_support_ticket(
            channels,
            category_id=category_id,
            user_id=user_id,
        )
        if existing:
            cid = str(existing.get("id") or "")
            client.edit_original(
                interaction["token"],
                content=(
                    f"Tu as déjà un ticket aide ouvert : <#{cid}>\n"
                    "Ferme-le avant d'en ouvrir un nouveau."
                ),
            )
            return

        username = str(user.get("username") or "membre")
        channel_name = sanitize_support_channel_name(username)
        bot_id = discord_application_id(
            getattr(client.settings, "discord_bot_token", "") or ""
        )
        if not bot_id:
            raise RuntimeError("Impossible de déterminer l'ID du bot")

        staff_role = client.support_staff_role_id()
        overwrites = build_ticket_overwrites(
            everyone_id=guild_id,
            opener_user_id=user_id,
            bot_user_id=bot_id,
            staff_role_id=staff_role,
        )
        created = client.create_guild_channel(
            guild_id,
            name=channel_name,
            parent_id=category_id,
            topic=support_ticket_topic_for_user(user_id),
            permission_overwrites=overwrites,
        )
        ticket_id = str(created.get("id") or "")
        if not ticket_id:
            raise RuntimeError("Salon ticket créé sans id")

        staff_mention = f"<@&{staff_role}>" if staff_role else ""
        payload = build_support_ticket_payload(
            opener_mention=f"<@{user_id}>",
            staff_mention=staff_mention,
        )
        post_body: dict[str, Any] = {
            "embeds": payload["embeds"],
            "components": payload["components"],
        }
        if staff_mention:
            post_body["content"] = staff_mention
        client.post_channel_payload(ticket_id, post_body)

        client.edit_original(
            interaction["token"],
            content=(
                f"✅ Ticket aide créé : <#{ticket_id}>\n"
                "Décris ton problème dans ce salon."
            ),
        )
        log.info(
            "support_ticket_opened",
            user_id=user_id,
            channel_id=ticket_id,
            channel_name=channel_name,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("support_open_failed", user_id=user_id, error=str(exc)[:240])
        client.edit_original(
            interaction["token"],
            content=(
                "❌ Impossible de créer le ticket pour le moment.\n"
                "Vérifie que le bot a **Gérer les salons** sur la catégorie support."
            ),
        )


def handle_support_close(
    client: DiscordInteractionClient,
    interaction: dict[str, Any],
    *,
    already_deferred: bool = False,
) -> None:
    user = _interaction_user(interaction)
    user_id = str(user.get("id") or "").strip()
    channel_id = str(interaction.get("channel_id") or "").strip()
    if not user_id or not channel_id:
        msg = "Impossible de fermer ce ticket."
        if already_deferred:
            client.edit_original(interaction["token"], content=msg)
        else:
            client.respond_ephemeral(interaction["id"], interaction["token"], msg)
        return

    if not already_deferred:
        client.defer_ephemeral(interaction["id"], interaction["token"])

    try:
        channel = client.get_channel(channel_id)
        opener_id = parse_support_opener_id(channel.get("topic"))
        is_opener = opener_id == user_id
        is_staff = _is_support_staff(interaction, client)
        if not is_opener and not is_staff:
            client.edit_original(
                interaction["token"],
                content="❌ Seul l'auteur du ticket ou le staff peut le fermer.",
            )
            return

        collected: list[dict[str, Any]] = []
        before: str | None = None
        for _ in range(20):
            batch = client.list_channel_messages(
                channel_id, limit=100, before=before
            )
            if not batch:
                break
            collected.extend(batch)
            before = str(batch[-1].get("id") or "") or None
            if len(batch) < 100:
                break

        transcript = format_ticket_transcript(collected)
        transcript_bytes = transcript.encode("utf-8")
        dm_target = opener_id or user_id
        try:
            client.send_dm_payload(
                dm_target,
                {
                    "content": (
                        "Votre ticket aide a été fermé.\n"
                        "Voici un transcript du ticket."
                    ),
                },
                attachments=[("log.txt", transcript_bytes, "text/plain")],
            )
        except Exception as dm_exc:  # noqa: BLE001
            log.warning(
                "support_transcript_dm_failed",
                user_id=dm_target,
                error=str(dm_exc)[:200],
            )

        client.delete_channel(channel_id)
        client.edit_original(
            interaction["token"],
            content="✅ Ticket fermé. Un transcript t'a été envoyé en message privé.",
        )
        log.info(
            "support_ticket_closed",
            closed_by=user_id,
            opener_id=opener_id,
            channel_id=channel_id,
            messages=len(collected),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "support_close_failed",
            user_id=user_id,
            channel_id=channel_id,
            error=str(exc)[:240],
        )
        client.edit_original(
            interaction["token"],
            content=(
                "❌ Impossible de fermer le ticket pour le moment.\n"
                f"Détail : `{str(exc)[:120]}`"
            ),
        )


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
            handle_reglement_accept(
                client, interaction, already_deferred=already_deferred
            )
            return
        if custom_id == RECRUIT_OPEN:
            handle_recruit_open(
                client, interaction, already_deferred=already_deferred
            )
            return
        if custom_id == RECRUIT_CLOSE:
            handle_recruit_close(
                client, interaction, already_deferred=already_deferred
            )
            return
        if custom_id == SUPPORT_OPEN:
            handle_support_open(
                client, interaction, already_deferred=already_deferred
            )
            return
        if custom_id == SUPPORT_CLOSE:
            handle_support_close(
                client, interaction, already_deferred=already_deferred
            )
            return
        if custom_id == WHOP_CLAIM:
            handle_whop_claim_button(client, interaction)
            return
        if custom_id.startswith(WHOP_CHECKOUT_PREFIX):
            handle_whop_checkout_button(client, interaction)
            return

    if interaction_type == 5:
        custom_id = str(interaction.get("data", {}).get("custom_id", ""))
        if custom_id == ALERT_CREATE_MODAL:
            handle_alert_create_modal(client, interaction)
            return
        if custom_id.startswith(ALERT_EDIT_MODAL_PREFIX):
            handle_alert_edit_modal(client, interaction)
            return
        if custom_id == WHOP_CLAIM_MODAL:
            handle_whop_claim_modal(client, interaction)
            return

    client.respond_ephemeral(
        interaction["id"],
        interaction["token"],
        "Interaction non gérée.",
    )

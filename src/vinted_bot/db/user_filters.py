"""Filtres privés utilisateur + plans d'abonnement."""

from __future__ import annotations

from typing import Any, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from vinted_bot.db.models import DiscordMemberPlan, UserFilter, UserFilterAlert

# Clés internes : starter / premium(=Pro) / elite(=Pro+)
PLAN_LIMITS: dict[str, int | None] = {
    "starter": 0,  # aucun filtre privé (offre marketing)
    "premium": 10,  # Pro
    "elite": 30,  # Pro+
}

VALID_PLANS = frozenset(PLAN_LIMITS)

# Alias marketing / Discord → clés internes
_PLAN_ALIASES: dict[str, str] = {
    "pro": "premium",
    "pro+": "elite",
    "proplus": "elite",
    "pro_plus": "elite",
}


def normalize_plan(plan: str | None) -> str:
    p = (plan or "starter").strip().lower().replace(" ", "")
    p = _PLAN_ALIASES.get(p, p)
    return p if p in VALID_PLANS else "starter"


def plan_filter_limit(plan: str | None) -> int | None:
    return PLAN_LIMITS[normalize_plan(plan)]


def get_or_create_member_plan(
    session: Session,
    discord_user_id: int,
    *,
    discord_username: str | None = None,
    default_plan: str = "starter",
) -> DiscordMemberPlan:
    row = session.scalar(
        select(DiscordMemberPlan).where(
            DiscordMemberPlan.discord_user_id == int(discord_user_id)
        )
    )
    if row is not None:
        if discord_username and row.discord_username != discord_username:
            row.discord_username = discord_username
        return row
    row = DiscordMemberPlan(
        discord_user_id=int(discord_user_id),
        discord_username=discord_username,
        plan=normalize_plan(default_plan),
    )
    session.add(row)
    session.flush()
    return row


def set_member_plan(
    session: Session,
    discord_user_id: int,
    plan: str,
    *,
    discord_username: str | None = None,
    subscription_active: bool | None = None,
    whop_membership_id: str | None = None,
) -> DiscordMemberPlan:
    row = get_or_create_member_plan(
        session, discord_user_id, discord_username=discord_username
    )
    row.plan = normalize_plan(plan)
    if discord_username:
        row.discord_username = discord_username
    if subscription_active is not None:
        row.subscription_active = bool(subscription_active)
    if whop_membership_id is not None:
        row.whop_membership_id = (whop_membership_id or "").strip() or None
    session.flush()
    return row


def deactivate_all_user_filters(session: Session, discord_user_id: int) -> int:
    """Met en pause tous les filtres d'un membre (ex. fin d'abo Whop)."""
    rows = list_user_filters(session, discord_user_id, active_only=True)
    for row in rows:
        row.is_active = False
    session.flush()
    return len(rows)

def save_member_dm_dashboard(
    session: Session,
    discord_user_id: int,
    *,
    dm_channel_id: str,
    dm_dashboard_message_id: str,
    discord_username: str | None = None,
) -> DiscordMemberPlan:
    row = get_or_create_member_plan(
        session, discord_user_id, discord_username=discord_username
    )
    row.dm_channel_id = str(dm_channel_id)
    row.dm_dashboard_message_id = str(dm_dashboard_message_id)
    if discord_username:
        row.discord_username = discord_username
    session.flush()
    return row


def count_user_filters(session: Session, discord_user_id: int) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(UserFilter)
            .where(UserFilter.discord_user_id == int(discord_user_id))
        )
        or 0
    )


def list_user_filters(
    session: Session,
    discord_user_id: int,
    *,
    active_only: bool = False,
) -> list[UserFilter]:
    stmt = (
        select(UserFilter)
        .where(UserFilter.discord_user_id == int(discord_user_id))
        .order_by(UserFilter.id.asc())
    )
    if active_only:
        stmt = stmt.where(UserFilter.is_active.is_(True))
    return list(session.scalars(stmt).all())


def filter_display_number(
    session: Session,
    *,
    discord_user_id: int,
    filter_id: int,
) -> int:
    """Numéro affiché 1..N pour l'utilisateur (pas l'id autoincrement DB)."""
    rows = list_user_filters(session, discord_user_id)
    for idx, row in enumerate(rows, start=1):
        if int(row.id) == int(filter_id):
            return idx
    return 1


def get_user_filter(
    session: Session,
    *,
    filter_id: int,
    discord_user_id: int,
) -> UserFilter | None:
    return session.scalar(
        select(UserFilter).where(
            UserFilter.id == int(filter_id),
            UserFilter.discord_user_id == int(discord_user_id),
        )
    )


def create_user_filter(
    session: Session,
    *,
    discord_user_id: int,
    brand: str | None = None,
    model: str | None = None,
    category: str | None = None,
    keyword: str | None = None,
    max_price_eur: float | None = None,
    min_price_eur: float | None = None,
    name: str | None = None,
) -> UserFilter:
    row = UserFilter(
        discord_user_id=int(discord_user_id),
        brand=(brand or "").strip() or None,
        model=(model or "").strip() or None,
        category=(category or "").strip() or None,
        keyword=(keyword or "").strip() or None,
        max_price_eur=max_price_eur,
        min_price_eur=min_price_eur,
        name=(name or "").strip() or None,
        is_active=True,
    )
    session.add(row)
    session.flush()
    return row


def update_user_filter(
    session: Session,
    row: UserFilter,
    *,
    brand: str | None = None,
    model: str | None = None,
    category: str | None = None,
    keyword: str | None = None,
    max_price_eur: float | None = None,
    min_price_eur: float | None = None,
    name: str | None = None,
    clear_max_price: bool = False,
) -> UserFilter:
    if brand is not None:
        row.brand = brand.strip() or None
    if model is not None:
        row.model = model.strip() or None
    if category is not None:
        row.category = category.strip() or None
    if keyword is not None:
        row.keyword = keyword.strip() or None
    if name is not None:
        row.name = name.strip() or None
    if clear_max_price:
        row.max_price_eur = None
    elif max_price_eur is not None:
        row.max_price_eur = max_price_eur
    if min_price_eur is not None:
        row.min_price_eur = min_price_eur
    session.flush()
    return row


def delete_user_filter(session: Session, row: UserFilter) -> None:
    session.delete(row)
    session.flush()


def toggle_user_filter(session: Session, row: UserFilter) -> UserFilter:
    row.is_active = not bool(row.is_active)
    session.flush()
    return row


def list_all_active_filters(session: Session) -> list[UserFilter]:
    return list(
        session.scalars(
            select(UserFilter).where(UserFilter.is_active.is_(True)).order_by(UserFilter.id)
        ).all()
    )


def already_alerted(
    session: Session,
    *,
    filter_id: int,
    vinted_id: int,
) -> bool:
    row = session.scalar(
        select(UserFilterAlert.id).where(
            UserFilterAlert.filter_id == int(filter_id),
            UserFilterAlert.vinted_id == int(vinted_id),
        )
    )
    return row is not None


def record_filter_alert(
    session: Session,
    *,
    filter_id: int,
    discord_user_id: int,
    vinted_id: int,
) -> UserFilterAlert:
    row = UserFilterAlert(
        filter_id=int(filter_id),
        discord_user_id=int(discord_user_id),
        vinted_id=int(vinted_id),
    )
    session.add(row)
    session.flush()
    return row


def filter_has_criteria(row: UserFilter) -> bool:
    return any(
        [
            row.brand,
            row.model,
            row.category,
            row.keyword,
            row.max_price_eur is not None,
            row.min_price_eur is not None,
        ]
    )


def summarize_filter(row: UserFilter, *, display_number: int | None = None) -> str:
    bits: list[str] = []
    if row.brand:
        bits.append(f"marque={row.brand}")
    if row.model:
        bits.append(f"modèle={row.model}")
    if row.category:
        bits.append(f"cat={row.category}")
    if row.keyword:
        bits.append(f"mot-clé={row.keyword}")
    if row.min_price_eur is not None:
        bits.append(f"min={row.min_price_eur:.0f}€")
    if row.max_price_eur is not None:
        bits.append(f"max={row.max_price_eur:.0f}€")
    status = "✅" if row.is_active else "⏸"
    label = row.name or "Filtre"
    detail = ", ".join(bits) if bits else "(vide)"
    num = display_number if display_number is not None else int(row.id)
    return f"{status} `#{num}` **{label}** — {detail}"

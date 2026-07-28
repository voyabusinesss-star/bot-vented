"""Comptes Vinted liés aux membres Discord."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from vinted_bot.db.models import MemberVintedAccount


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_member_vinted_account(
    session: Session, discord_user_id: int
) -> MemberVintedAccount | None:
    return session.scalar(
        select(MemberVintedAccount).where(
            MemberVintedAccount.discord_user_id == discord_user_id,
            MemberVintedAccount.is_active.is_(True),
        )
    )


def upsert_member_vinted_account(
    session: Session,
    *,
    discord_user_id: int,
    storage_state: dict[str, Any],
    discord_username: str | None = None,
    vinted_username: str | None = None,
) -> MemberVintedAccount:
    now = _utcnow()
    stmt = (
        insert(MemberVintedAccount)
        .values(
            discord_user_id=discord_user_id,
            discord_username=discord_username,
            vinted_username=vinted_username,
            storage_state=storage_state,
            linked_at=now,
            updated_at=now,
            is_active=True,
        )
        .on_conflict_do_update(
            index_elements=[MemberVintedAccount.discord_user_id],
            set_={
                "discord_username": discord_username,
                "vinted_username": vinted_username,
                "storage_state": storage_state,
                "updated_at": now,
                "is_active": True,
                "autobuy_validated_at": None,
            },
        )
        .returning(MemberVintedAccount)
    )
    account = session.scalar(stmt)
    assert account is not None
    return account


def list_active_member_vinted_accounts(session: Session) -> list[MemberVintedAccount]:
    return list(
        session.scalars(
            select(MemberVintedAccount)
            .where(MemberVintedAccount.is_active.is_(True))
            .order_by(MemberVintedAccount.linked_at.desc())
        ).all()
    )


def deactivate_member_vinted_account(session: Session, discord_user_id: int) -> bool:
    account = session.scalar(
        select(MemberVintedAccount).where(
            MemberVintedAccount.discord_user_id == discord_user_id
        )
    )
    if account is None:
        return False
    account.is_active = False
    account.autobuy_validated_at = None
    account.updated_at = _utcnow()
    return True


def update_member_storage_state(
    session: Session,
    discord_user_id: int,
    storage_state: dict[str, Any],
) -> MemberVintedAccount | None:
    """Rafraîchit les cookies Vinted (ex. après captcha)."""
    account = get_member_vinted_account(session, discord_user_id)
    if account is None:
        return None
    account.storage_state = storage_state
    account.updated_at = _utcnow()
    return account

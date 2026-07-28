"""Tokens de liaison Vinted self-service."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from vinted_bot.db.models import VintedLinkToken

TOKEN_TTL_MINUTES = 20


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def create_vinted_link_token(
    session: Session,
    *,
    discord_user_id: int,
    discord_username: str | None = None,
) -> VintedLinkToken:
    now = _utcnow()
    row = VintedLinkToken(
        token=secrets.token_urlsafe(32),
        discord_user_id=discord_user_id,
        discord_username=discord_username,
        created_at=now,
        expires_at=now + timedelta(minutes=TOKEN_TTL_MINUTES),
    )
    session.add(row)
    session.flush()
    return row


def get_valid_vinted_link_token(session: Session, token: str) -> VintedLinkToken | None:
    now = _utcnow()
    return session.scalar(
        select(VintedLinkToken).where(
            VintedLinkToken.token == token,
            VintedLinkToken.used_at.is_(None),
            VintedLinkToken.expires_at > now,
        )
    )


def mark_vinted_link_token_used(session: Session, token: str) -> None:
    session.execute(
        update(VintedLinkToken)
        .where(VintedLinkToken.token == token)
        .values(used_at=_utcnow())
    )

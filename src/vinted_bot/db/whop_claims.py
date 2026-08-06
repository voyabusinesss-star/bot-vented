"""Persistance des claims Whop en attente (paiement sans Discord lié)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from vinted_bot.db.models import WhopPendingClaim


def normalize_claim_email(email: str | None) -> str | None:
    text = (email or "").strip().lower()
    return text or None


def upsert_pending_claim(
    session: Session,
    *,
    membership_id: str,
    plan: str,
    product_id: str | None = None,
    email: str | None = None,
    license_key: str | None = None,
) -> WhopPendingClaim:
    mid = (membership_id or "").strip()
    if not mid:
        raise ValueError("membership_id requis")
    row = session.get(WhopPendingClaim, mid)
    if row is None:
        row = WhopPendingClaim(membership_id=mid, plan=plan)
        session.add(row)
    row.plan = (plan or "").strip() or row.plan
    if product_id is not None:
        row.product_id = (product_id or "").strip() or None
    email_n = normalize_claim_email(email)
    if email_n:
        row.email = email_n
    if license_key is not None:
        key = (license_key or "").strip() or None
        row.license_key = key
    # Nouveau paiement → reouvrir le claim
    row.claimed_at = None
    row.claimed_discord_user_id = None
    session.flush()
    return row


def find_open_claim(
    session: Session,
    *,
    membership_id: str | None = None,
    email: str | None = None,
    license_key: str | None = None,
) -> WhopPendingClaim | None:
    if membership_id:
        mid = membership_id.strip()
        row = session.get(WhopPendingClaim, mid)
        if row is not None and row.claimed_at is None:
            return row
    email_n = normalize_claim_email(email)
    if email_n:
        row = session.scalar(
            select(WhopPendingClaim)
            .where(
                WhopPendingClaim.email == email_n,
                WhopPendingClaim.claimed_at.is_(None),
            )
            .order_by(WhopPendingClaim.created_at.desc())
            .limit(1)
        )
        if row is not None:
            return row
    key = (license_key or "").strip()
    if key:
        row = session.scalar(
            select(WhopPendingClaim)
            .where(
                WhopPendingClaim.license_key == key,
                WhopPendingClaim.claimed_at.is_(None),
            )
            .order_by(WhopPendingClaim.created_at.desc())
            .limit(1)
        )
        if row is not None:
            return row
    return None


def mark_claim_used(
    session: Session,
    membership_id: str,
    *,
    discord_user_id: int,
) -> None:
    row = session.get(WhopPendingClaim, (membership_id or "").strip())
    if row is None:
        return
    row.claimed_at = datetime.now(timezone.utc)
    row.claimed_discord_user_id = int(discord_user_id)
    session.flush()

"""One-shot: unlock Ami (and AMI) listings silent-marked as posted."""
from __future__ import annotations

from sqlalchemy import text

from vinted_bot.db.session import session_scope


def main() -> None:
    sql = text(
        """
        UPDATE listings
        SET discord_posted_at = NULL
        WHERE discord_posted_at IS NOT NULL
          AND is_active IS TRUE
          AND lower(coalesce(brand, '')) LIKE '%ami%'
          AND last_seen_at > NOW() - INTERVAL '14 days'
        RETURNING id, vinted_id, brand, price_cents
        """
    )
    with session_scope() as session:
        rows = session.execute(sql).fetchall()
    print(f"ami_repost_unlocked={len(rows)}")
    for row in rows[:20]:
        print(row.id, row.vinted_id, row.brand, row.price_cents)


if __name__ == "__main__":
    main()

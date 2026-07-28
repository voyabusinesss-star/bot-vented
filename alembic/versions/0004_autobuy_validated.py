"""autobuy validated timestamp on member accounts

Revision ID: 0004_autobuy_validated
Revises: 0003_member_vinted
Create Date: 2026-07-25

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_autobuy_validated"
down_revision: Union[str, Sequence[str], None] = "0003_member_vinted"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "member_vinted_accounts",
        sa.Column("autobuy_validated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("member_vinted_accounts", "autobuy_validated_at")

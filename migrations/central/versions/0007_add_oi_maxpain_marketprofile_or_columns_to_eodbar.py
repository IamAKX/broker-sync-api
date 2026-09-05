"""add OI/max-pain/market-profile/opening-range columns to EodBar

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-05

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Same DECIMAL(14, 4) precision as 0006's turnover/ATP columns (EodBar's own
# OHLC convention) — strike prices/Max Pain/VAH-POC-VAL/OR High-Low are all
# plain rupee-price figures, nothing here needs more precision than that.
_COLUMNS = [
    "call_strike_highest_oi", "call_strike_with_second_highest_oi",
    "put_strike_with_second_highest_oi", "today_put_highest_strike",
    "max_pain", "vah", "poc", "val", "or_high", "or_low",
]


def upgrade() -> None:
    for name in _COLUMNS:
        op.add_column("EodBar", sa.Column(name, sa.DECIMAL(14, 4), nullable=True))


def downgrade() -> None:
    for name in reversed(_COLUMNS):
        op.drop_column("EodBar", name)

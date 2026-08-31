"""add LMV turnover/avg-traded-price metric columns to EodBar

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-31

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
# "0005" is a branchpoint with two children — f7b0766c55ad (already applied:
# added Instrument/TradingCalendar/EodBar, the table this migration extends)
# and this one. Must chain onto f7b0766c55ad specifically, not 0005 directly,
# or "upgrade head" has two divergent heads to choose between instead of one
# straight line.
down_revision: Union[str, None] = "f7b0766c55ad"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS = ["avg_rate", "patp", "pwatp", "pmatp", "cwatp", "cmatp", "day_to", "pdto", "cwto", "pwto"]


def upgrade() -> None:
    for name in _COLUMNS:
        op.add_column("EodBar", sa.Column(name, sa.DECIMAL(14, 4), nullable=True))


def downgrade() -> None:
    for name in reversed(_COLUMNS):
        op.drop_column("EodBar", name)

"""add current_login_at and last_login_at columns to User

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-18

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("User", sa.Column("current_login_at", sa.DateTime(), nullable=True))
    op.add_column("User", sa.Column("last_login_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("User", "last_login_at")
    op.drop_column("User", "current_login_at")

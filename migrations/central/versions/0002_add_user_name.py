"""add name column to User

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-18

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "User",
        sa.Column("name", sa.String(200), nullable=False, server_default=""),
    )
    op.alter_column("User", "name", server_default=None)


def downgrade() -> None:
    op.drop_column("User", "name")

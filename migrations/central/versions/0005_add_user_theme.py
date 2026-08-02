"""add theme column to User

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-02

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "User",
        sa.Column("theme", sa.String(10), nullable=False, server_default="light"),
    )


def downgrade() -> None:
    op.drop_column("User", "theme")

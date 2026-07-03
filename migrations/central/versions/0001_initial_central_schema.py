"""initial central schema: Tenant, User, RefreshToken

Revision ID: 0001
Revises:
Create Date: 2026-07-03

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mssql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "Tenant",
        sa.Column("id", mssql.UNIQUEIDENTIFIER(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("schema_name", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("sysutcdatetime()")),
        sa.UniqueConstraint("schema_name", name="uq_tenant_schema_name"),
    )

    op.create_table(
        "User",
        sa.Column("id", mssql.UNIQUEIDENTIFIER(), primary_key=True),
        sa.Column("tenant_id", mssql.UNIQUEIDENTIFIER(), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("sysutcdatetime()")),
        sa.ForeignKeyConstraint(["tenant_id"], ["Tenant.id"], name="fk_user_tenant"),
        sa.UniqueConstraint("email", name="uq_user_email"),
    )

    op.create_table(
        "RefreshToken",
        sa.Column("id", mssql.UNIQUEIDENTIFIER(), primary_key=True),
        sa.Column("user_id", mssql.UNIQUEIDENTIFIER(), nullable=False),
        sa.Column("token_hash", sa.String(255), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["User.id"], name="fk_refreshtoken_user"),
    )


def downgrade() -> None:
    op.drop_table("RefreshToken")
    op.drop_table("User")
    op.drop_table("Tenant")

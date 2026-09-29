"""One-off backfill: add the StrategySignal.exit_price column to every
EXISTING tenant schema (see app/models/tenant.py::StrategySignal.exit_price
for what it's for and why it was added).

Tenant tables are provisioned once at signup via provisioning_service.py's
create_all(checkfirst=True) machinery, which — per that module's own
docstring — creates MISSING TABLES for an already-provisioned schema but
never alters an EXISTING table's columns. A brand new tenant signing up
after this change gets exit_price for free (create_all emits the whole
current StrategySignal DDL); every tenant schema created before it needs
this one-time ALTER TABLE instead.

Idempotent and safe to re-run: `ADD COLUMN IF NOT EXISTS` is a no-op on a
schema that already has the column (e.g. a previous partial run). Adding a
nullable column with no default is a metadata-only change on Postgres (no
table rewrite, no long lock), so this is safe to run against a live
database without a maintenance window.

Run on the server:  .venv/bin/python scripts/add_strategy_signal_exit_price_column.py
"""

import asyncio
import sys

from sqlalchemy import select, text

sys.path.insert(0, ".")

from app.db.central_session import CentralSessionLocal  # noqa: E402
from app.models.central import Tenant  # noqa: E402


async def main() -> None:
    async with CentralSessionLocal() as session:
        schemas = (await session.execute(select(Tenant.schema_name))).scalars().all()

        for schema in schemas:
            try:
                await session.execute(
                    text(
                        f'ALTER TABLE "{schema}"."StrategySignal" '
                        f'ADD COLUMN IF NOT EXISTS exit_price NUMERIC(18, 4)'
                    )
                )
                await session.commit()
            except Exception as exc:
                await session.rollback()
                print(f"{schema:20s}  ERROR: {exc}")
                continue
            print(f"{schema:20s}  OK")


if __name__ == "__main__":
    asyncio.run(main())

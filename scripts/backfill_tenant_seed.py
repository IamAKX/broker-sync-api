"""Backfill: seed every EXISTING tenant (signed up before tenant_seed_
service shipped) with the admin tenant's starter data — same one-time copy
signup now does automatically, see app.services.tenant_seed_service's
module docstring for the full rationale and safety model.

Idempotent and safe to re-run: seed_tenant_from_admin only ever writes into
a table that is currently empty for that tenant/user, so a tenant that
already has its own data in a given table is left alone (reported as
"skipped"), and a tenant already fully seeded is a no-op the second time.

Skips: the admin's own tenant (nothing to seed into itself), and any
schema in _SKIP_SCHEMAS — test/CI fixture tenants that should never receive
a copy of the admin's real strategies.

Run on the server:  .venv/bin/python scripts/backfill_tenant_seed.py
"""

import asyncio
import sys

from sqlalchemy import select, text

sys.path.insert(0, ".")

from app.core.deps import ADMIN_EMAIL  # noqa: E402
from app.db.central_session import CentralSessionLocal  # noqa: E402
from app.models.central import Tenant, User  # noqa: E402
from app.services.tenant_seed_service import seed_tenant_from_admin  # noqa: E402

# e2e_dss: created by the backend's own end-to-end test suite (fake
# @example.com email) — reseeded/wiped by test runs regardless, and
# shouldn't receive a copy of the admin's real proprietary strategies.
_SKIP_SCHEMAS = {"e2e_dss"}


async def main() -> None:
    async with CentralSessionLocal() as session:
        rows = (
            await session.execute(
                select(Tenant, User).join(User, User.tenant_id == Tenant.id)
            )
        ).all()

        for tenant, user in rows:
            schema = tenant.schema_name
            if schema in _SKIP_SCHEMAS:
                print(f"{schema:20s}  SKIPPED (excluded)")
                continue
            if user.email.strip().lower() == ADMIN_EMAIL:
                print(f"{schema:20s}  SKIPPED (this is the admin tenant)")
                continue

            try:
                report = await seed_tenant_from_admin(session, schema, user.id)
                await session.commit()
            except Exception as exc:
                await session.rollback()
                print(f"{schema:20s}  ERROR: {exc}")
                continue

            if not report:
                print(f"{schema:20s}  nothing to do (no admin tenant found)")
                continue
            print(f"{schema:20s} ({user.email}):")
            for table, outcome in report.items():
                print(f"    {table:26s} {outcome}")


if __name__ == "__main__":
    asyncio.run(main())

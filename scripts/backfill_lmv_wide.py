"""Backfill LmvDailySnapshotWide from LmvDailySnapshot for every tenant schema.

Idempotent (INSERT ... ON CONFLICT DO UPDATE) and safe to re-run. Reads
nothing but writes the derived table; the EAV source is never touched.

Run on the server:  .venv/bin/python scripts/backfill_lmv_wide.py
"""

import asyncio
import sys

from sqlalchemy import select, text

sys.path.insert(0, ".")

from app.db.central_session import CentralSessionLocal  # noqa: E402
from app.db.tenant_session import get_tenant_session_for_schema  # noqa: E402
from app.models.central import Tenant  # noqa: E402
from app.services.provisioning_service import ensure_tenant_schema_tables  # noqa: E402

# One statement per schema. jsonb_object_agg pivots all metrics for a
# (trade_date, stock) into one object; ::float8 matches the API's float()
# path so cached and freshly-pivoted responses are byte-identical.
_BACKFILL_SQL = text(
    """
    INSERT INTO "LmvDailySnapshotWide" (trade_date, stock_id, symbol, display_name, metrics, updated_at)
    SELECT
        l.trade_date,
        l.stock_id,
        s.symbol,
        s.display_name,
        jsonb_object_agg(
            m.name,
            CASE
                WHEN l.value_number IS NOT NULL THEN to_jsonb(l.value_number::float8)
                WHEN l.value_text   IS NOT NULL THEN to_jsonb(l.value_text)
                ELSE 'null'::jsonb
            END
        ),
        now()
    FROM "LmvDailySnapshot" l
    JOIN "Stock"  s ON s.id = l.stock_id
    JOIN "Metric" m ON m.id = l.metric_id
    GROUP BY l.trade_date, l.stock_id, s.symbol, s.display_name
    ON CONFLICT (trade_date, stock_id) DO UPDATE SET
        symbol       = EXCLUDED.symbol,
        display_name = EXCLUDED.display_name,
        metrics      = EXCLUDED.metrics,
        updated_at   = now();
    """
)

_COUNTS_SQL = text(
    """
    SELECT
      (SELECT count(DISTINCT trade_date) FROM "LmvDailySnapshot")     AS eav_dates,
      (SELECT count(DISTINCT trade_date) FROM "LmvDailySnapshotWide")  AS wide_dates,
      (SELECT count(*) FROM "LmvDailySnapshotWide")                    AS wide_rows
    """
)


async def main() -> None:
    async with CentralSessionLocal() as central:
        tenants = (await central.execute(select(Tenant))).scalars().all()

    for t in tenants:
        schema = t.schema_name
        gen = get_tenant_session_for_schema(schema)
        session = await gen.__anext__()
        try:
            # make sure LmvDailySnapshotWide exists in this schema
            await ensure_tenant_schema_tables(session, schema)
            # raw text() SQL is NOT subject to schema_translate_map — pin the
            # search_path so the unqualified table names resolve to this tenant
            await session.execute(text(f'SET search_path TO "{schema}"'))
            await session.execute(_BACKFILL_SQL)
            await session.commit()
            row = (await session.execute(_COUNTS_SQL)).one()
            print(
                f"{schema:20s}  eav_dates={row.eav_dates}  "
                f"wide_dates={row.wide_dates}  wide_rows={row.wide_rows}"
            )
        except Exception as exc:  # keep going for the other tenants
            await session.rollback()
            print(f"{schema:20s}  ERROR: {exc}")
        finally:
            await gen.aclose()


if __name__ == "__main__":
    asyncio.run(main())

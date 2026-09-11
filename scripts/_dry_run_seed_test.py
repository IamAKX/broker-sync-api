"""One-off, throwaway integration check for tenant_seed_service's actual SQL
against the real Postgres instance — NOT part of the permanent test suite.
Creates two disposable schemas (zz_seed_test_admin / zz_seed_test_target),
puts one representative fixture row in each of Group A + both Group B
shapes into the "admin" schema, runs the seed helpers directly against the
disposable schemas (bypassing the ADMIN_EMAIL lookup, which can't be
faked without colliding with the real admin's unique email), asserts the
copy landed correctly, then drops both schemas. Prints "DRY RUN OK" at the
end if every assertion passed.
"""

import asyncio
import sys
import uuid

from sqlalchemy import text

sys.path.insert(0, ".")

from app.db.central_session import CentralSessionLocal  # noqa: E402
from app.services.provisioning_service import _create_schema_and_tables_sync  # noqa: E402
from app.services.tenant_seed_service import _seed_group_a, _seed_group_b  # noqa: E402

ADMIN_SCHEMA = "zz_seed_test_admin"
TARGET_SCHEMA = "zz_seed_test_target"
ADMIN_USER_ID = uuid.uuid4()
TARGET_USER_ID = uuid.uuid4()


async def main() -> None:
    async with CentralSessionLocal() as session:
        conn = await session.connection()
        for schema in (ADMIN_SCHEMA, TARGET_SCHEMA):
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await conn.run_sync(_create_schema_and_tables_sync, schema)
        await session.commit()

        # Fixture data in the "admin" schema.
        await session.execute(text(f'INSERT INTO "{ADMIN_SCHEMA}"."Stock" (symbol, display_name, is_active) '
                                    "VALUES ('RELIANCE', 'Reliance Industries', true)"))
        await session.execute(text(f'INSERT INTO "{ADMIN_SCHEMA}"."Metric" (name, data_type, is_active) '
                                    "VALUES ('LTP', 'number', true)"))
        stock_id = await session.scalar(text(f'SELECT id FROM "{ADMIN_SCHEMA}"."Stock" LIMIT 1'))
        metric_id = await session.scalar(text(f'SELECT id FROM "{ADMIN_SCHEMA}"."Metric" LIMIT 1'))
        await session.execute(text(
            f'INSERT INTO "{ADMIN_SCHEMA}"."HistoricalStockValue" (trade_date, stock_id, metric_id, value_number) '
            "VALUES ('2026-01-01', :s, :m, 100)"
        ), {"s": stock_id, "m": metric_id})
        await session.execute(text(
            f'INSERT INTO "{ADMIN_SCHEMA}"."OpeningRangeCapture" (trade_date, stock_id, high, low, window_minutes) '
            "VALUES ('2026-01-01', :s, 101, 99, 15)"
        ), {"s": stock_id})
        await session.execute(text(
            f'INSERT INTO "{ADMIN_SCHEMA}"."LmvDailySnapshot" (trade_date, stock_id, metric_id, value_number) '
            "VALUES ('2026-01-01', :s, :m, 100)"
        ), {"s": stock_id, "m": metric_id})
        await session.execute(text(
            f'INSERT INTO "{ADMIN_SCHEMA}"."LmvDailySnapshotWide" (trade_date, stock_id, symbol, display_name, metrics) '
            "VALUES ('2026-01-01', :s, 'RELIANCE', 'Reliance Industries', '{}'::jsonb)"
        ), {"s": stock_id})
        await session.execute(text(
            f'INSERT INTO "{ADMIN_SCHEMA}"."SavedStrategy" (id, user_id, name, active, category, columns, row_filter) '
            "VALUES (:id, :uid, 'Test Strategy', true, 'Daily', '[{\"name\":\"Col1\"}]'::jsonb, '[]'::jsonb)"
        ), {"id": uuid.uuid4(), "uid": ADMIN_USER_ID})
        await session.execute(text(
            f'INSERT INTO "{ADMIN_SCHEMA}"."FormulaVariable" (id, user_id, name, formula) '
            "VALUES (:id, :uid, 'Threshold', '[{\"type\":\"num\",\"value\":\"1\"}]'::jsonb)"
        ), {"id": uuid.uuid4(), "uid": ADMIN_USER_ID})
        await session.execute(text(
            f'INSERT INTO "{ADMIN_SCHEMA}"."UserSetting" (user_id, key, value) '
            "VALUES (:uid, 'formula_builder_fields', '[{\"code\":\"PMATP\"}]'::jsonb)"
        ), {"uid": ADMIN_USER_ID})
        await session.commit()

        # Run the actual seed helpers.
        report_a = await _seed_group_a(session, ADMIN_SCHEMA, TARGET_SCHEMA)
        report_b = await _seed_group_b(session, ADMIN_SCHEMA, ADMIN_USER_ID, TARGET_SCHEMA, TARGET_USER_ID)
        await session.commit()
        print("Group A report:", report_a)
        print("Group B report:", report_b)

        # ── Assertions ──────────────────────────────────────────────────
        errors = []

        def check(label, cond):
            if not cond:
                errors.append(label)

        t_stock = (await session.execute(text(f'SELECT id, symbol FROM "{TARGET_SCHEMA}"."Stock"'))).all()
        check("Stock copied with same id", len(t_stock) == 1 and t_stock[0][0] == stock_id and t_stock[0][1] == "RELIANCE")

        t_metric_count = await session.scalar(text(f'SELECT COUNT(*) FROM "{TARGET_SCHEMA}"."Metric"'))
        check("Metric copied", t_metric_count == 1)

        t_hsv = await session.scalar(text(f'SELECT value_number FROM "{TARGET_SCHEMA}"."HistoricalStockValue"'))
        check("HistoricalStockValue value preserved", float(t_hsv) == 100.0)

        t_orc = await session.scalar(text(f'SELECT COUNT(*) FROM "{TARGET_SCHEMA}"."OpeningRangeCapture"'))
        check("OpeningRangeCapture copied", t_orc == 1)

        t_lds = await session.scalar(text(f'SELECT COUNT(*) FROM "{TARGET_SCHEMA}"."LmvDailySnapshot"'))
        check("LmvDailySnapshot copied", t_lds == 1)

        t_ldsw = await session.scalar(text(f'SELECT metrics FROM "{TARGET_SCHEMA}"."LmvDailySnapshotWide" LIMIT 1'))
        check("LmvDailySnapshotWide copied", t_ldsw is not None)

        # Sequence must be past the copied id — inserting a fresh Stock row
        # (relying on the default) must not collide with the copied id.
        new_id = await session.scalar(text(
            f'INSERT INTO "{TARGET_SCHEMA}"."Stock" (symbol, display_name, is_active) '
            "VALUES ('TCS', 'TCS', true) RETURNING id"
        ))
        check(f"Stock sequence advanced past copied id (new_id={new_id} > stock_id={stock_id})", new_id > stock_id)

        strat = (await session.execute(text(
            f'SELECT id, user_id, name FROM "{TARGET_SCHEMA}"."SavedStrategy"'
        ))).all()
        check("SavedStrategy copied exactly once", len(strat) == 1)
        if strat:
            check("SavedStrategy user_id remapped to target user", strat[0][1] == TARGET_USER_ID)
            check("SavedStrategy name preserved", strat[0][2] == "Test Strategy")

        var = (await session.execute(text(
            f'SELECT user_id, name FROM "{TARGET_SCHEMA}"."FormulaVariable"'
        ))).all()
        check("FormulaVariable copied + remapped", len(var) == 1 and var[0][0] == TARGET_USER_ID)

        setting = (await session.execute(text(
            f'SELECT user_id, key, value FROM "{TARGET_SCHEMA}"."UserSetting"'
        ))).all()
        check("UserSetting copied + remapped", len(setting) == 1 and setting[0][0] == TARGET_USER_ID)

        # Re-running must be a safe no-op (everything now non-empty).
        report_a2 = await _seed_group_a(session, ADMIN_SCHEMA, TARGET_SCHEMA)
        report_b2 = await _seed_group_b(session, ADMIN_SCHEMA, ADMIN_USER_ID, TARGET_SCHEMA, TARGET_USER_ID)
        check("Re-run Group A is a no-op", all("skipped" in v for v in report_a2.values()))
        # InceptionStrategy/InceptionFormulaVariable never had admin fixture
        # rows to begin with, so their correct (and still idempotent) outcome
        # both times is "nothing to copy", not "skipped".
        check("Re-run Group B is a no-op",
              all(("skipped" in v or "nothing to copy" in v) for v in report_b2.values()))

        for schema in (ADMIN_SCHEMA, TARGET_SCHEMA):
            await session.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await session.commit()

        if errors:
            print("FAILED:")
            for e in errors:
                print("  -", e)
            sys.exit(1)
        print("DRY RUN OK")


if __name__ == "__main__":
    asyncio.run(main())

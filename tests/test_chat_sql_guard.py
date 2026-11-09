import pytest

from app.services.chat.sql_guard import UnsafeSqlError, validate_and_prepare


def ok(sql: str):
    return validate_and_prepare(sql, max_rows=200)


def test_simple_select_is_wrapped_with_limit():
    v = ok("SELECT symbol, close FROM eod_daily WHERE trade_date = '2026-10-23' ORDER BY close DESC LIMIT 5;")
    assert v.sql.startswith("SELECT * FROM (")
    assert v.sql.endswith("LIMIT 201")
    assert "eod_daily" in v.display_sql


def test_cte_window_jsonb_and_subquery_allowed():
    ok(
        """WITH last AS (SELECT max(trade_date) d FROM eod_daily)
           SELECT symbol, (metrics->>'Close')::numeric AS c,
                  lag(trade_date) OVER (PARTITION BY symbol ORDER BY trade_date)
           FROM lmv_daily, last WHERE trade_date = last.d"""
    )


def test_joins_between_allowed_views():
    ok("SELECT s.symbol, e.close FROM stocks s JOIN eod_daily e ON e.symbol = s.symbol")


@pytest.mark.parametrize(
    "sql",
    [
        "",
        "   ;  ",
        "DROP TABLE eod_daily",
        "DELETE FROM eod_daily",
        "UPDATE eod_daily SET close = 1",
        "INSERT INTO stocks VALUES ('x')",
        "SELECT 1; SELECT 2",
        "SELECT 1; DROP TABLE stocks",
        "CREATE TABLE x AS SELECT 1",
        "SELECT * INTO newtab FROM stocks",
        "SELECT * FROM stocks FOR UPDATE",
        "SELECT * FROM pg_user",
        "SELECT * FROM pg_catalog.pg_shadow",
        "SELECT * FROM information_schema.tables",
        'SELECT * FROM "Stock"',
        'SELECT * FROM akash_dss."Stock"',
        "SELECT * FROM public.\"User\"",
        "SELECT * FROM \"User\"",
        "SELECT pg_sleep(30)",
        "SELECT set_config('search_path', 'x', false)",
        "SELECT current_setting('server_version')",
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT * FROM generate_series(1, 100000000)",
        "COPY stocks TO '/tmp/x'",
        "SET statement_timeout = 0",
        "SELECT * FROM stocks WHERE symbol = $1",
        "WITH x AS (DELETE FROM stocks RETURNING *) SELECT * FROM x",
        "SELECT dblink('host=evil', 'select 1')",
    ],
)
def test_dangerous_or_invalid_sql_rejected(sql):
    with pytest.raises(UnsafeSqlError):
        ok(sql)


def test_overlong_sql_rejected():
    with pytest.raises(UnsafeSqlError):
        ok("SELECT " + ", ".join(["1"] * 5000))


def test_comment_smuggling_does_not_hide_second_statement():
    with pytest.raises(UnsafeSqlError):
        ok("SELECT 1 /* ; */ ; DROP TABLE stocks")


def test_executed_text_is_the_rerendered_ast_not_the_raw_input():
    v = ok("select symbol   from   stocks -- trailing comment")
    assert "--" not in v.sql

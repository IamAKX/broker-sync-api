"""Validation of the model-generated SQL — the hard safety boundary of the chatbot.

The prompt asks the model to write read-only queries against a handful of
curated views, but nothing in the prompt is trusted: every query is parsed
here and rejected unless it is exactly one SELECT over the allow-listed
views using allow-listed functions. The validated AST is re-rendered and THAT
text is what runs (never the model's raw string), so parser differences
between sqlglot and Postgres can't smuggle anything through. sql_runner adds
the second layer (read-only transaction, statement timeout, per-request temp
views that already carry the tenant/user scoping).
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

ALLOWED_RELATIONS: frozenset[str] = frozenset(
    {
        "stocks",
        "eod_daily",
        "lmv_daily",
        "lmv_live",
        "stock_sectors",
        "strategy_signals",
        "my_strategies",
        "eod_futures_bars",
        "market_holidays",
    }
)

# Functions sqlglot has no dedicated node for (they parse as exp.Anonymous).
# Anything not listed here is rejected — this is what blocks pg_sleep,
# pg_read_file, set_config, current_setting, dblink, lo_*, etc.
ALLOWED_ANONYMOUS_FUNCTIONS: frozenset[str] = frozenset(
    {
        "jsonb_extract_path_text",
        "jsonb_extract_path",
        "jsonb_typeof",
        "jsonb_object_keys",
        "jsonb_array_length",
        "greatest",
        "least",
        "nullif",
        "initcap",
        "lpad",
        "rpad",
        "abs",
        "sign",
        "trunc",
        "ceil",
        "floor",
        "sqrt",
        "power",
        "ln",
        "log",
        "exp",
        "mod",
        "div",
        "to_char",
        "to_date",
        "date_part",
        "age",
        "regr_slope",
        "corr",
        "stddev",
        "stddev_pop",
        "stddev_samp",
        "variance",
        "var_pop",
        "var_samp",
        "percentile_cont",
        "percentile_disc",
        "mode",
        "bool_and",
        "bool_or",
        "string_agg",
        "array_agg",
        "ntile",
        "percent_rank",
        "cume_dist",
        "first_value",
        "last_value",
        "nth_value",
    }
)

_FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.Command,
    exp.Set,
    exp.Copy,
    exp.TruncateTable,
    exp.Use,
    exp.Placeholder,
    exp.Parameter,
    exp.Pragma,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
)

MAX_SQL_CHARS = 6000


class UnsafeSqlError(ValueError):
    """The generated SQL was rejected; the message is safe to show the model
    (it drives the one-shot repair attempt) and to log."""


@dataclass(frozen=True)
class ValidatedSql:
    sql: str  # the executable, re-rendered, LIMIT-wrapped statement
    display_sql: str  # the re-rendered query without the wrapper, for the UI


def validate_and_prepare(raw_sql: str, *, max_rows: int) -> ValidatedSql:
    text = (raw_sql or "").strip().rstrip(";").strip()
    if not text:
        raise UnsafeSqlError("The query is empty.")
    if len(text) > MAX_SQL_CHARS:
        raise UnsafeSqlError(f"The query is too long (limit {MAX_SQL_CHARS} characters).")

    try:
        statements = sqlglot.parse(text, read="postgres")
    except SqlglotError as exc:
        raise UnsafeSqlError(f"The query is not valid PostgreSQL: {exc}") from exc
    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        raise UnsafeSqlError("Exactly one SELECT statement is allowed.")
    tree = statements[0]

    if not isinstance(tree, exp.Query):
        raise UnsafeSqlError("Only SELECT queries are allowed.")

    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}

    for node in tree.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            raise UnsafeSqlError(f"{type(node).__name__} is not allowed; use a plain SELECT.")
        if isinstance(node, exp.Select):
            if node.args.get("into"):
                raise UnsafeSqlError("SELECT ... INTO is not allowed.")
            if node.args.get("locks"):
                raise UnsafeSqlError("Row locking (FOR UPDATE/SHARE) is not allowed.")
        if isinstance(node, exp.Table):
            _check_table(node, cte_names)
        elif isinstance(node, exp.Anonymous):
            name = str(node.name).lower()
            if name not in ALLOWED_ANONYMOUS_FUNCTIONS:
                raise UnsafeSqlError(f"Function {name}() is not allowed.")

    display = tree.sql(dialect="postgres")
    wrapped = f"SELECT * FROM ({display}) AS chat_q LIMIT {int(max_rows) + 1}"
    return ValidatedSql(sql=wrapped, display_sql=display)


def _check_table(node: exp.Table, cte_names: set[str]) -> None:
    if node.args.get("db") or node.args.get("catalog"):
        raise UnsafeSqlError("Schema-qualified tables are not allowed; use the documented views only.")
    if not isinstance(node.this, exp.Identifier):
        raise UnsafeSqlError("Table-valued functions are not allowed.")
    name = node.name.lower()
    if name in cte_names:
        return
    if name not in ALLOWED_RELATIONS:
        raise UnsafeSqlError(
            f"Relation '{node.name}' is not available. Use only: {', '.join(sorted(ALLOWED_RELATIONS))}."
        )

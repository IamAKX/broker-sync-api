"""Server-side formula evaluator for Inception Strategy Builder columns and
row filters — a from-scratch port (not a shared import; broker-file-sync and
broker-sync-api are separate deployments) of the row-wise subset of the
desktop client's services/strategy_engine.py: same token shapes (col/num/op/
paren/func/var/self — see that module's docstring in the client repo), same
function vocabulary the client's Strategy Builder UI actually exposes as
buttons (MIN/MAX/ABS/ROUND/FLOOR/CEIL/SUM/IF — screens/strategy_builder.py's
FUNCTIONS list; the client engine itself supports a much larger legacy
vocabulary that was never wired to a UI button, so it's not carried over
here), plus the client's row-filter comparison vocabulary (screens/
strategy_builder.py's COND_OPS: > < >= <= == != AND OR NOT).

Computed server-side (per your instruction) because Inception's dataset is
426 instruments x thousands of trading days — pulling that to the desktop
client to compute strategy columns the way LMV's client-side engine does
would mean pulling far more data than the client ever needs to render one
screen. app.services.inception_service calls apply_strategy_columns() once
per row it's about to return from /inception/snapshot or /inception/hmv.

Deliberately NOT ported: SUM_ALL/MIN_ALL/etc (cross-row, no analogue needed —
an Inception "row" is one instrument on one date, and the aggregate windows
LMV's _DAYS/day_history family exist for are already precomputed as Group A/
B columns here, so a formula just references the column directly instead of
asking for a runtime aggregate).

── Compile-time validation ──────────────────────────────────────────────────
Per your instruction: "OPEN, HIGH, LOW, CLOSE, VOL, OPENINT" get a dummy
float during formula validation (e.g. Strategy Builder's compile-test,
before any real row is being evaluated). DUMMY_ROW extends this to every
raw + Group A/B field, so a compile-test never fails merely because a
particular field happens to be unpopulated for the instrument/date the test
happens to run against.
"""

import math
import re

from app.services.inception_columns import RAW_FIELDS, all_derived_metric_names

_FUNC_MAP = {
    "MIN": "min", "MAX": "max", "ABS": "abs", "ROUND": "round",
    "FLOOR": "_floor", "CEIL": "_ceil", "CEILING": "_ceil",
    "SUM": "_sum", "IF": "_if", "IIF": "_if",
}


def _floor(x, *_):
    return math.floor(x)


def _ceil(x, *_):
    return math.ceil(x)


def _sum(*args):
    return sum(args)


def _if(cond, a, b):
    return a if cond else b


_EVAL_BUILTINS = {
    "__builtins__": {},
    "min": min, "max": max, "abs": abs, "round": round,
    "_floor": _floor, "_ceil": _ceil, "_sum": _sum, "_if": _if,
    "True": True, "False": False, "None": None,
}

_COND_OP_MAP = {"AND": "and", "OR": "or", "NOT": "not"}

DUMMY_ROW: dict = {f: 1.0 for f in RAW_FIELDS}
DUMMY_ROW.update({name: 1.0 for name in all_derived_metric_names()})


def _col_literal(raw) -> str:
    if raw is None or raw == "":
        return "None"
    try:
        return str(float(raw))
    except (TypeError, ValueError):
        return repr(str(raw))


def _expand_var_tokens(tokens: list, variables_by_name: dict, _seen: frozenset = frozenset()) -> list:
    """Inline {"type": "var", "value": name} tokens with that InceptionFormulaVariable's
    own formula, recursively — same cyclic/unknown-name "drop, don't crash"
    behavior as the client's _expand_var_tokens."""
    if not any(tok.get("type") == "var" for tok in tokens):
        return tokens
    out = []
    for tok in tokens:
        if tok.get("type") != "var":
            out.append(tok)
            continue
        name = tok.get("value")
        if name in _seen or not variables_by_name or name not in variables_by_name:
            continue
        inner = _expand_var_tokens(variables_by_name[name], variables_by_name, _seen | {name})
        if inner:
            out.append({"type": "paren", "value": "("})
            out.extend(inner)
            out.append({"type": "paren", "value": ")"})
    return out


def _tokens_to_expr(tokens: list, row_data: dict, self_value, variables_by_name: dict | None) -> str:
    tokens = _expand_var_tokens(tokens, variables_by_name or {})
    parts = []
    for tok in tokens:
        t = tok.get("type")
        v = tok.get("value", "")
        if t == "col":
            parts.append(_col_literal(row_data.get(v)))
        elif t == "self":
            parts.append(_col_literal(self_value))
        elif t == "num" or t == "paren":
            parts.append(v)
        elif t == "op":
            # Word operators (and/or/not) need surrounding whitespace or they
            # merge into an adjacent numeric/identifier token — e.g. a bare
            # "100.0and10.0" is a syntax error, not "100.0 and 10.0". Purely
            # symbolic operators (> < >= <= == != + - * / , etc.) don't need
            # this since they can't merge with an identifier character.
            parts.append(f" {_COND_OP_MAP[v]} " if v in _COND_OP_MAP else v)
        elif t == "func":
            fname = v.rstrip("(").upper()
            parts.append(_FUNC_MAP.get(fname, fname.lower()) + "(")
    return "".join(parts)


_SAFE_EXPR_RE = re.compile(r"^[0-9a-zA-Z_+\-*/(),.\s<>=!]*$")


def _safe_eval(expr: str):
    if not expr or not _SAFE_EXPR_RE.match(expr):
        return None
    try:
        return eval(expr, _EVAL_BUILTINS)  # noqa: S307 — namespace is a closed builtins-free dict
    except Exception:
        return None


def evaluate(tokens: list, row_data: dict, self_value=None, variables_by_name: dict | None = None):
    """Return numeric/string result, or None on error/empty formula."""
    if not tokens:
        return None
    expr = _tokens_to_expr(tokens, row_data, self_value, variables_by_name)
    return _safe_eval(expr)


def evaluate_condition(tokens: list, row_data: dict, variables_by_name: dict | None = None) -> bool:
    """Row-filter / fmt-rule condition — same token vocabulary as evaluate()
    plus COND_OPS comparison/boolean operators. Empty condition = always true
    (no filter applied), matching the client's row-filter semantics."""
    if not tokens:
        return True
    result = evaluate(tokens, row_data, variables_by_name=variables_by_name)
    return bool(result)


def compile_check(tokens: list, variables_by_name: dict | None = None) -> tuple[bool, str | None]:
    """Structural/type validation against DUMMY_ROW (see module docstring) —
    used by the Strategy Builder editor's "compile test" before real data is
    necessarily available for the field(s) referenced."""
    if not tokens:
        return False, "Formula is empty."
    expr = _tokens_to_expr(tokens, DUMMY_ROW, self_value=1.0, variables_by_name=variables_by_name)
    if not expr:
        return False, "Formula is empty."
    if not _SAFE_EXPR_RE.match(expr):
        return False, "Formula contains an unsupported character or token."
    try:
        eval(expr, _EVAL_BUILTINS)  # noqa: S307
    except ZeroDivisionError:
        return False, "Formula divides by zero."
    except Exception as exc:
        return False, f"Formula failed to evaluate: {exc}"
    return True, None


def apply_strategy_columns(
    strategies: list[dict], base_row: dict, variables_by_name: dict | None = None
) -> dict:
    """strategies: active InceptionStrategy dicts (id/name/columns/row_filter).
    base_row: {field_name: value} for one instrument/date (raw + Group A/B).
    Returns {strategy_column_name: computed_value} for every column of every
    strategy whose row_filter passes for this row — same semantics as the
    client's apply_strategies, minus the conditional-format color output
    (Inception's grid screens don't paint cells, only compute values).
    """
    extra: dict = {}
    for strategy in strategies:
        row_so_far = {**base_row, **extra}
        if not evaluate_condition(strategy.get("row_filter", []), row_so_far, variables_by_name):
            continue
        for col in strategy.get("columns", []):
            name = col.get("name")
            if not name:
                continue
            row_so_far = {**base_row, **extra}
            extra[name] = evaluate(col.get("formula", []), row_so_far, variables_by_name=variables_by_name)
    return extra

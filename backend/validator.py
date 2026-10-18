import operator
import re

import sqlglot
from sqlglot import exp

_BANNED = tuple(
    getattr(exp, n)
    for n in ("Insert", "Update", "Delete", "Drop", "Create", "Alter", "Command", "Merge")
    if hasattr(exp, n)
)

_OPS = {
    "=": operator.eq, "==": operator.eq, "!=": operator.ne,
    ">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le,
}


def check_sql(sql: str, known: dict, dialect: str) -> str:
    """Return "" if the SQL is safe and valid, otherwise an error message."""
    if not sql.strip():
        return "Model returned empty SQL"
    try:
        stmts = [s for s in sqlglot.parse(sql, read=dialect) if s]
    except Exception as e:
        return f"Syntax error: {str(e)[:200]}"

    if len(stmts) != 1:
        return "Exactly one statement is allowed"
    tree = stmts[0]
    if not isinstance(tree, exp.Select):
        return "Only a SELECT statement is allowed"
    if tree.find(*_BANNED):
        return "Write operations are not allowed"

    ctes = {c.alias.lower() for c in tree.find_all(exp.CTE)}
    tables = {t.name.lower() for t in tree.find_all(exp.Table)} - ctes
    unknown_tables = [t for t in tables if t not in known]
    if unknown_tables:
        return f"Unknown table(s): {', '.join(unknown_tables)}"

    allowed = {c.lower() for t in tables for c in known[t]}
    aliases = {a.alias.lower() for a in tree.find_all(exp.Alias)}
    bad = sorted({
        c.name for c in tree.find_all(exp.Column)
        if c.name and c.name != "*" and c.name.lower() not in allowed | aliases
    })
    if bad:
        return f"Unknown column(s): {', '.join(bad)}"
    return ""


def summarize(rows: list):
    """Reduce a query result to numbers. Returns (numbers, note).
    No rows = 0 violations. Non-numeric rows (a 'find bad rows' query) = row count."""
    if not rows:
        return [0.0], "no rows returned (treated as 0)"
    values = [0 if v is None else v for v in rows[0]]
    try:
        return [float(v) for v in values], ""
    except (TypeError, ValueError):
        return [float(len(rows))], f"{len(rows)} row(s) returned"


def evaluate(rows: list, expected: str):
    """Single-DB check: compare the result to a number or operator + number."""
    m = re.match(r"^\s*(==|=|!=|>=|<=|>|<)?\s*(-?\d+(?:\.\d+)?)\s*$", str(expected))
    if not m:
        return "ERROR", f"Cannot understand expected value '{expected}'"
    op, target = _OPS[m.group(1) or "="], float(m.group(2))
    nums, note = summarize(rows)
    ok = all(op(n, target) for n in nums)
    prefix = f"{note}. " if note else ""
    return ("PASS" if ok else "FAIL"), f"{prefix}Result {nums} vs expected {expected}"


def compare(src_rows: list, tgt_rows: list, expected: str):
    """Source-to-target check: 'match' (equal) or a number (max allowed difference)."""
    s, ns = summarize(src_rows)
    t, nt = summarize(tgt_rows)
    exp_str = str(expected).strip().lower()
    try:
        tol = 0.0 if exp_str in ("", "match") else float(exp_str)
    except ValueError:
        return "ERROR", f"Cannot understand expected '{expected}' (use 'match' or a number)"
    if len(s) != len(t):
        return "ERROR", f"Source returned {len(s)} value(s), target returned {len(t)}"

    ok = all(abs(a - b) <= tol for a, b in zip(s, t))
    notes = "".join(f"{lbl}: {n}. " for lbl, n in (("Source", ns), ("Target", nt)) if n)
    return ("PASS" if ok else "FAIL"), f"{notes}Source {s} vs Target {t} (tolerance {tol:g})"
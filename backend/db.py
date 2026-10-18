from decimal import Decimal

from sqlalchemy import create_engine, inspect, text

_SQLGLOT = {"postgresql": "postgres", "mysql": "mysql", "sqlite": "sqlite", "mssql": "tsql"}
MAX_ROWS = 1000
TIMEOUT_SECONDS = 30

CONN = {}  # "source" / "target" -> {"engine": Engine, "schema": {table: [columns]}}


def connect(side: str, url: str) -> dict:
    """Connect, test the connection, cache the schema for this side."""
    eng = create_engine(url, pool_pre_ping=True)
    with eng.connect() as conn:
        conn.execute(text("SELECT 1"))
    insp = inspect(eng)
    schema = {
        t: [{"name": c["name"], "type": str(c["type"])} for c in insp.get_columns(t)]
        for t in insp.get_table_names()
    }
    CONN[side] = {"engine": eng, "schema": schema}
    return schema


def is_connected(side: str) -> bool:
    return side in CONN


def _eng(side: str):
    return CONN[side]["engine"]


def dialect(side: str) -> str:
    return _eng(side).dialect.name


def sqlglot_dialect(side: str) -> str:
    return _SQLGLOT.get(dialect(side), dialect(side))


def known_columns(side: str) -> dict:
    """{table_lower: [column names]} for SQL validation."""
    return {t.lower(): [c["name"] for c in cols] for t, cols in CONN[side]["schema"].items()}


def table_columns(side: str, table: str) -> list:
    """Accepts 'table' or 'schema.table'."""
    eng = _eng(side)
    schema, _, name = table.rpartition(".")
    if eng.dialect.name == "sqlite":
        schema = ""
    insp = inspect(eng)
    return [
        {"name": c["name"], "type": str(c["type"])}
        for c in insp.get_columns(name, schema=schema or None)
    ]


def schema_text(table: str, cols: list) -> str:
    """Same shape as the training data: events(event_id INTEGER, ...)"""
    return f"{table}(" + ", ".join(f"{c['name']} {c['type']}" for c in cols) + ")"


def _plain(v):
    if v is None or isinstance(v, (int, float, str, bool)):
        return v
    if isinstance(v, Decimal):
        return float(v)
    return str(v)


def run_query(side: str, sql: str):
    """Run a validated SELECT on one side. Returns (columns, rows)."""
    with _eng(side).connect() as conn:
        d = dialect(side)
        if d == "postgresql":
            conn.execute(text("SET TRANSACTION READ ONLY"))
            conn.execute(text(f"SET statement_timeout = {TIMEOUT_SECONDS * 1000}"))
        elif d == "mysql":
            conn.execute(text(f"SET SESSION max_execution_time = {TIMEOUT_SECONDS * 1000}"))
        res = conn.execute(text(sql))
        cols = list(res.keys())
        rows = [[_plain(v) for v in r] for r in res.fetchmany(MAX_ROWS)]
    return cols, rows
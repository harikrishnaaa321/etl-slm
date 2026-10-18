from typing import TypedDict

from langgraph.graph import END, StateGraph

import db
import llm
import validator

MAX_RETRIES = 2  # 1 attempt + 2 retries


class State(TypedDict, total=False):
    side: str       # "source" | "target"
    table: str
    rule: str
    schema_text: str
    cols: list
    sql: str
    attempts: int
    resample: bool
    error: str
    columns: list
    rows: list
    status: str     # "ERROR" once retries are exhausted
    detail: str


def generate(s: State):
    n = s.get("attempts", 0) + 1
    try:
        cols = db.table_columns(s["side"], s["table"])
    except Exception:
        cols = []
    if not cols:
        msg = f"Table '{s['table']}' not found in {s['side']} database"
        return {"sql": "", "error": msg, "status": "ERROR", "detail": msg, "attempts": n}

    name = s["table"].rpartition(".")[2]
    schema_text = db.schema_text(name, cols)
    sampled = n > 1 or s.get("resample", False)  # re-runs and retries vary the SQL
    sql = llm.generate_sql(s["rule"], schema_text, db.sqlglot_dialect(s["side"]), 1 if sampled else 0)
    return {
        "sql": sql, "schema_text": schema_text, "cols": [c["name"] for c in cols],
        "attempts": n, "error": "", "status": "", "detail": "", "rows": [], "columns": [],
    }


def validate(s: State):
    known = db.known_columns(s["side"])
    known[s["table"].rpartition(".")[2].lower()] = s["cols"]
    err = validator.check_sql(s["sql"], known, db.sqlglot_dialect(s["side"]))
    exhausted = bool(err) and s["attempts"] > MAX_RETRIES
    return {"error": err, "status": "ERROR" if exhausted else "", "detail": err if exhausted else ""}


def execute(s: State):
    try:
        cols, rows = db.run_query(s["side"], s["sql"])
    except Exception as e:
        err = str(e).splitlines()[0][:300]
        exhausted = s["attempts"] > MAX_RETRIES
        return {"error": err, "status": "ERROR" if exhausted else "", "detail": err if exhausted else ""}
    return {"error": "", "columns": cols, "rows": rows}


def route(ok_node: str):
    def _route(s: State):
        if not s.get("error"):
            return ok_node
        return "end" if s.get("status") == "ERROR" else "generate"  # retry
    return _route


wf = StateGraph(State)
wf.add_node("generate", generate)
wf.add_node("validate", validate)
wf.add_node("execute", execute)

wf.set_entry_point("generate")
wf.add_conditional_edges(
    "generate",
    lambda s: "end" if s.get("status") == "ERROR" else "validate",
    {"end": END, "validate": "validate"},
)
wf.add_conditional_edges("validate", route("execute"),
                         {"execute": "execute", "generate": "generate", "end": END})
wf.add_conditional_edges("execute", route("end"),
                         {"end": END, "generate": "generate"})

app_graph = wf.compile()


def _run_side(side: str, table: str, rule_text: str, resample: bool) -> dict:
    return app_graph.invoke(
        {"side": side, "table": table, "rule": rule_text, "attempts": 0, "resample": resample}
    )


def _pack(s: dict) -> dict:
    return {
        "table": s.get("table"), "rule": s.get("rule"), "sql": s.get("sql", ""),
        "columns": s.get("columns", []), "rows": s.get("rows", []),
        "attempts": s.get("attempts", 0),
        "error": s.get("detail", "") if s.get("status") == "ERROR" else "",
    }


def run_rule(rule: dict, resample: bool = False) -> dict:
    """Run one mapping row. Single check if no target_table, otherwise source vs target."""
    compare = bool(rule.get("target_table"))
    out = {**rule, "mode": "compare" if compare else "single"}

    out["source"] = _pack(_run_side(
        "source", rule["source_table"],
        rule["rule"].replace("{col}", rule["source_column"]), resample))
    if compare:
        out["target"] = _pack(_run_side(
            "target", rule["target_table"],
            rule["rule"].replace("{col}", rule["target_column"]), resample))

    errors = [f"{k}: {out[k]['error']}" for k in ("source", "target") if k in out and out[k]["error"]]
    if errors:
        out["status"], out["detail"] = "ERROR", " | ".join(errors)
    elif compare:
        out["status"], out["detail"] = validator.compare(
            out["source"]["rows"], out["target"]["rows"], rule["expected"])
    else:
        out["status"], out["detail"] = validator.evaluate(out["source"]["rows"], rule["expected"])
    return out
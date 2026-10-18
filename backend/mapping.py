import pandas as pd

ALIASES = {"table_name": "source_table"}  # old header still works


def _clean(v) -> str:
    return "" if pd.isna(v) else str(v).strip()


def parse_mapping(file) -> list:
    """Columns: source_table | source_column | target_table | target_column | rule | expected
    Blank target_table = single-DB check on the source."""
    df = pd.read_excel(file)
    cols = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    df.columns = [ALIASES.get(c, c) for c in cols]

    missing = {"source_table", "rule"} - set(df.columns)
    if missing:
        raise ValueError(f"Missing column(s) in Excel: {', '.join(sorted(missing))}")

    rules = []
    for n, (_, row) in enumerate(df.iterrows(), start=2):
        get = lambda k: _clean(row[k]) if k in df.columns else ""
        src, rule = get("source_table"), get("rule")
        if not src or not rule:
            continue
        tgt = get("target_table")
        scol = get("source_column")
        tcol = get("target_column") or scol
        if "{col}" in rule and not scol:
            raise ValueError(f"Excel row {n}: rule uses {{col}} but source_column is empty")
        rules.append({
            "id": len(rules) + 1,
            "source_table": src,
            "target_table": tgt,
            "source_column": scol,
            "target_column": tcol,
            "rule": rule,
            "expected": get("expected") or ("match" if tgt else "0"),
        })
    if not rules:
        raise ValueError("No rules found in the Excel file")
    return rules
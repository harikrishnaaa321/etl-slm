import os
from urllib.parse import quote_plus
import time
import pandas as pd
import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv()
API = os.getenv("BACKEND_URL", "http://localhost:8000")
ICON = {"PASS": "Pass", "FAIL": "Fail", "ERROR": "Error"}

st.set_page_config(page_title="ETL Mapping Validator", layout="wide")
st.title("ETL Mapping Validator")

for key in ("schema_source", "schema_target", "rules", "results"):
    st.session_state.setdefault(key, None)


def call(method, path, timeout=60, **kw):
    try:
        r = requests.request(method, API + path, timeout=timeout, **kw)
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        try:
            st.error(e.response.json().get("detail", e.response.text))
        except Exception:
            st.error(e.response.text)
    except Exception as e:
        st.error(f"Backend error: {e}")
    return None


def connection_form(side: str):
    kind = st.selectbox("Database", ["PostgreSQL", "MySQL", "SQLite"], key=f"{side}-kind")
    if kind == "SQLite":
        path = st.text_input("SQLite file path", key=f"{side}-path")
        url = f"sqlite:///{path}"
    else:
        c1, c2, c3, c4, c5 = st.columns(5)
        host = c1.text_input("Host", "localhost", key=f"{side}-host")
        port = c2.text_input("Port", "5432" if kind == "PostgreSQL" else "3306", key=f"{side}-port-{kind}")
        user = c3.text_input("User", key=f"{side}-user")
        pw = c4.text_input("Password", type="password", key=f"{side}-pw")
        name = c5.text_input("Database", key=f"{side}-name")
        driver = "postgresql+psycopg2" if kind == "PostgreSQL" else "mysql+pymysql"
        url = f"{driver}://{quote_plus(user)}:{quote_plus(pw)}@{host}:{port}/{name}"

    if st.button(f"Connect {side}", key=f"{side}-connect"):
        data = call("POST", f"/connect/{side}", json={"url": url})
        if data:
            st.session_state[f"schema_{side}"] = data["schema"]
            st.session_state.results = None

    schema = st.session_state[f"schema_{side}"]
    if schema is not None:
        st.success(f"{side.capitalize()} connected. Found {len(schema)} tables.")
        for table, cols in schema.items():
            with st.expander(f"{table} ({len(cols)} columns)"):
                st.dataframe(pd.DataFrame(cols), hide_index=True)


def show_side(label: str, s: dict):
    st.write(f"**{label}:** `{s['table']}`   |   **Attempts:** {s['attempts']}")
    st.caption(s["rule"])
    if s["sql"]:
        st.code(s["sql"], language="sql")
    if s["error"]:
        st.error(s["error"])
    elif s["columns"]:
        if s["rows"]:
            st.dataframe(pd.DataFrame(s["rows"], columns=s["columns"]), hide_index=True)
        else:
            st.caption("Query executed and returned no rows.")


def first_row(s):
    if not s:
        return ""
    if s["error"]:
        return "ERROR"
    return str(s["rows"][0]) if s["rows"] else "(no rows)"

def fmt(seconds: float) -> str:
    seconds = int(max(seconds, 0))
    return f"{seconds // 60}m {seconds % 60:02d}s" if seconds >= 60 else f"{seconds}s"


def run_all(resample: bool = False):
    """Run rules one by one, showing a progress bar and the estimated time left."""
    rules = st.session_state.rules
    total = len(rules)
    bar = st.progress(0.0, text="Starting. The first rule also loads the model, so it takes longer.")
    start, durations, results = time.time(), [], []

    for n, rule in enumerate(rules, start=1):
        if n == 1:
            bar.progress(0.0, text=f"Running rule 1/{total} (loading the model on first use...)")
        else:
            left = (sum(durations[1:] or durations) / len(durations[1:] or durations)) * (total - n + 1)
            bar.progress((n - 1) / total,
                         text=f"Running rule {n}/{total} | elapsed {fmt(time.time() - start)} | about {fmt(left)} left")
        t0 = time.time()
        res = call("POST", f"/run/{rule['id']}", timeout=1800,
                   params={"resample": str(resample).lower()})
        if res is None:
            bar.empty()
            return None
        results.append(res)
        durations.append(time.time() - t0)

    bar.progress(1.0, text=f"Finished {total} rules in {fmt(time.time() - start)}")
    return results
# ---------- 1. Connect ----------
st.header("1. Connect to databases")
tab_s, tab_t = st.tabs(["Source database", "Target database (for source-to-target rules)"])
with tab_s:
    connection_form("source")
with tab_t:
    connection_form("target")

# ---------- 2. Upload ----------
st.header("2. Upload mapping document")
st.caption("Excel columns: source_table | source_column | target_table | target_column | rule | expected")
if st.session_state.schema_source is None:
    st.info("Connect the source database first.")
else:
    f = st.file_uploader("Mapping Excel", type=["xlsx"])
    if f and st.button("Read mapping"):
        data = call("POST", "/upload", files={"file": (f.name, f.getvalue())})
        if data:
            st.session_state.rules = data
            st.session_state.results = None
    if st.session_state.rules:
        st.dataframe(pd.DataFrame(st.session_state.rules), hide_index=True)

# ---------- 3. Run ----------
# ---------- 3. Run ----------
st.header("3. Generate SQL, execute and compare")
if st.session_state.rules:
    c1, c2, _ = st.columns([1, 1, 6])
    if c1.button("Run all rules", type="primary"):
        st.session_state.results = run_all()
    if st.session_state.results and c2.button("Run all again"):
        st.session_state.results = run_all(resample=True)
else:
    st.info("Upload a mapping document first.")

# ---------- 4. Results per rule ----------
if st.session_state.results:
    st.header("4. Query results")
    for i, r in enumerate(st.session_state.results):
        arrow = f"{r['source_table']} → {r['target_table']}" if r["mode"] == "compare" else r["source_table"]
        title = f"{ICON.get(r['status'], '')} #{r['id']} {arrow}: {r['rule']}  [{r['status']}]"
        with st.expander(title, expanded=True):
            st.write(f"**Expected:** {r['expected']}")
            if r["mode"] == "compare":
                left, right = st.columns(2)
                with left:
                    show_side("Source", r["source"])
                with right:
                    show_side("Target", r["target"])
            else:
                show_side("Source", r["source"])
            st.write(f"**Verdict:** {r['status']}. {r['detail']}")

            if st.button("Run again", key=f"rerun-{r['id']}"):
                with st.spinner("Generating new SQL and executing..."):
                    new = call("POST", f"/rerun/{r['id']}", timeout=600)
                if new:
                    st.session_state.results[i] = new
                    st.rerun()

    # ---------- 5. Final report ----------
    st.header("5. Final report")
    results = st.session_state.results
    counts = pd.Series([r["status"] for r in results]).value_counts()
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total rules", len(results))
    m2.metric("PASS", int(counts.get("PASS", 0)))
    m3.metric("FAIL", int(counts.get("FAIL", 0)))
    m4.metric("ERROR", int(counts.get("ERROR", 0)))

    report = pd.DataFrame([{
        "id": r["id"], "mode": r["mode"],
        "source_table": r["source_table"], "target_table": r["target_table"],
        "rule": r["rule"], "expected": r["expected"],
        "source_sql": r["source"]["sql"], "source_result": first_row(r["source"]),
        "target_sql": r.get("target", {}).get("sql", ""), "target_result": first_row(r.get("target")),
        "status": r["status"], "evidence": r["detail"],
    } for r in results])
    st.dataframe(report, hide_index=True, use_container_width=True)
    st.download_button("Download final report (CSV)", report.to_csv(index=False),
                       "validation_report.csv", "text/csv")
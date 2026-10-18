import io

from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel

import db
import graph
import mapping

app = FastAPI(title="ETL Mapping Validator")

STATE = {"rules": [], "results": []}


class ConnectIn(BaseModel):
    url: str


@app.post("/connect/{side}")
def connect(side: str, body: ConnectIn):
    if side not in ("source", "target"):
        raise HTTPException(404, "side must be 'source' or 'target'")
    try:
        schema = db.connect(side, body.url)
    except Exception as e:
        raise HTTPException(400, f"Connection failed: {str(e).splitlines()[0]}")
    STATE["results"] = []
    return {"dialect": db.dialect(side), "schema": schema}


@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    try:
        STATE["rules"] = mapping.parse_mapping(io.BytesIO(await file.read()))
    except Exception as e:
        raise HTTPException(400, str(e))
    STATE["results"] = []
    return STATE["rules"]


def _check_ready():
    if not db.is_connected("source"):
        raise HTTPException(400, "Connect the source database first")
    if not STATE["rules"]:
        raise HTTPException(400, "Upload a mapping document first")
    if any(r["target_table"] for r in STATE["rules"]) and not db.is_connected("target"):
        raise HTTPException(400, "Some rules have a target_table: connect the target database first")


@app.post("/run")
def run(resample: bool = False):
    _check_ready()
    STATE["results"] = [graph.run_rule(r, resample) for r in STATE["rules"]]
    return STATE["results"]

@app.post("/run/{rule_id}")
def run_one(rule_id: int, resample: bool = False):
    """Run a single rule. The frontend calls this in a loop to show progress."""
    _check_ready()
    rule = next((r for r in STATE["rules"] if r["id"] == rule_id), None)
    if rule is None:
        raise HTTPException(404, f"Rule {rule_id} not found")
    return graph.run_rule(rule, resample)

@app.post("/rerun/{rule_id}")
def rerun(rule_id: int):
    _check_ready()
    rule = next((r for r in STATE["rules"] if r["id"] == rule_id), None)
    if rule is None:
        raise HTTPException(404, f"Rule {rule_id} not found")
    new = graph.run_rule(rule, resample=True)
    STATE["results"] = [new if r["id"] == rule_id else r for r in STATE["results"]]
    return new


@app.get("/results")
def results():
    return STATE["results"]
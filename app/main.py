"""FastAPI app for the blood bank monitor. Thin: parse input, call qc / rules, return JSON.

The API stores nothing: the page sends its maintenance log with each request (SPEC section 6).
"""
import math
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

try:                       # works from the repo root (tests) ...
    from app import rules
except ImportError:        # ... and when Posit Connect runs app/ as the bundle root
    import rules

DATA_DIR = Path(__file__).resolve().parent / "data"     # bundled copies of data/*.csv
TELEMETRY = pd.read_csv(DATA_DIR / "fridge_telemetry.csv")
TESTS = pd.read_csv(DATA_DIR / "generator_tests.csv")
BUNDLED_LOG = pd.read_csv(DATA_DIR / "maintenance_log.csv", dtype=str, keep_default_na=False)

app = FastAPI(
    title="Blood bank equipment monitor",
    description="Fridge I-MR / capability and generator p-chart / dead time, with escalation tiers.",
    version="1.0.0",
)

# Let a browser frontend on another address (e.g. localhost:5173) call this API.
# Fine for a hackathon demo; list the real frontend URL before putting anything private here.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class AttentionRequest(BaseModel):
    """Body of POST /attention: log entries sent by the page, and an optional as-of time."""
    log: List[dict] = []
    as_of: Optional[str] = None


def json_safe(x):
    """Make a value JSON-safe: numpy -> Python, NaN / inf -> None, Timestamp -> text."""
    if isinstance(x, dict):
        return {str(k): json_safe(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [json_safe(v) for v in x]
    if isinstance(x, np.generic):
        x = x.item()
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        return None
    if isinstance(x, pd.Timestamp):
        return x.isoformat()
    return x


def build_log(entries=None):
    """Bundled log plus the entries the page sent. A bad entry is a 422."""
    try:
        return rules.make_log(BUNDLED_LOG, entries)
    except (ValueError, KeyError, TypeError) as err:
        raise HTTPException(status_code=422, detail=f"bad log entry: {err}")


def run(func, *args):
    """Call a rules function; ValueError becomes a clear 400 (bad as_of, too little data)."""
    try:
        return json_safe(func(*args))
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err))


@app.get("/health")
async def health():
    """Liveness check."""
    return {"status": "ok"}


@app.get("/log")
async def get_log():
    """The bundled maintenance log, so the page can seed its local log."""
    return json_safe(BUNDLED_LOG.to_dict("records"))


@app.get("/fridges/{unit_id}")
def get_fridge(unit_id: str, as_of: Optional[str] = None):
    """qc.fridge_summary for one unit, plus alerts and status (bundled log only)."""
    if unit_id not in set(TELEMETRY["unit_id"]):
        raise HTTPException(status_code=404, detail=f"unknown fridge {unit_id}")
    r = run(rules.fridge_status, TELEMETRY, unit_id, build_log(), as_of)
    return {**r.pop("summary"), **r}


@app.get("/generators/{gen_id}")
def get_generator(gen_id: str, as_of: Optional[str] = None):
    """qc.generator_summary for one generator, plus alerts and status (bundled log only)."""
    if gen_id not in set(TESTS["generator_id"]):
        raise HTTPException(status_code=404, detail=f"unknown generator {gen_id}")
    now = as_of or TELEMETRY["timestamp"].max()
    r = run(rules.generator_status, TESTS, gen_id, build_log(), now)
    return {**r.pop("summary"), **r}


@app.get("/attention")
def get_attention(as_of: Optional[str] = None):
    """Attention list for all units on the bundled log."""
    return run(rules.attention_list, TELEMETRY, TESTS, build_log(), as_of)


@app.post("/attention")
def post_attention(body: AttentionRequest):
    """Attention list on the bundled log plus the entries sent in the body."""
    return run(rules.attention_list, TELEMETRY, TESTS, build_log(body.log), body.as_of)


# The built React page (frontend/ -> app/static/). Mounted LAST so every API route above wins.
STATIC_DIR = Path(__file__).resolve().parent / "static"
if STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="page")

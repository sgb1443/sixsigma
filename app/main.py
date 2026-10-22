"""FastAPI app for the blood bank monitor. Thin: parse input, call qc / rules, return JSON.

The API stores nothing: the page sends its maintenance log with each request (SPEC section 6).
"""
import math
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

try:                       # works from the repo root (tests) ...
    from app import rules, simulate
except ImportError:        # ... and when Posit Connect runs app/ as the bundle root
    import rules
    import simulate

DATA_DIR = Path(__file__).resolve().parent / "data"     # bundled copies of data/ (and its subfolders)
SEED_MAX = 999_999_999

# The test scenarios. "folder" is inside app/data/ ("" = the main files); random has no files.
SCENARIOS = {
    "mixed": {"label": "Scenario 1: Mixed problems", "folder": "",
              "description": "Four fridges and two generators with different planted problems."},
    "healthy": {"label": "Scenario 2: All healthy", "folder": "all_healthy",
                "description": "Every unit behaves well: the tool should stay green."},
    "blackout": {"label": "Scenario 3: Blackout day", "folder": "blackout_day",
                 "description": "A grid outage in the last 24 h and a generator that failed its weekly start."},
    "random": {"label": "Scenario 4: Random data", "folder": None,
               "description": "Randomly generated week; the planted behaviours are shown so you can "
                              "check the tool"},
}
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
    """Body of POST /attention: log entries sent by the page, an optional as-of time, and the
    optional scenario (and seed for "random"); the same names also work as query parameters."""
    log: List[dict] = []
    as_of: Optional[str] = None
    scenario: Optional[str] = None
    seed: Optional[int] = Field(None, ge=0, le=SEED_MAX)


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


@lru_cache(maxsize=None)
def _file_scenario(key):
    """Load one scenario's three CSV files once."""
    folder = DATA_DIR / SCENARIOS[key]["folder"]
    return {"telemetry": pd.read_csv(folder / "fridge_telemetry.csv"),
            "tests": pd.read_csv(folder / "generator_tests.csv"),
            "log": pd.read_csv(folder / "maintenance_log.csv", dtype=str, keep_default_na=False)}


@lru_cache(maxsize=16)
def _random_scenario(seed):
    """Generate the random scenario for this seed once (the same seed always gives the same data)."""
    fridge, gens, log, answer_key = simulate.random_scenario(seed)
    return {"telemetry": fridge, "tests": gens, "log": log, "answer_key": answer_key}


def get_scenario(key, seed=None):
    """The data of a scenario. Unknown scenario: 404. Random without a seed: 422."""
    if key not in SCENARIOS:
        raise HTTPException(status_code=404, detail=f"unknown scenario '{key}'; "
                            f"use one of: {', '.join(SCENARIOS)}")
    if key != "random":
        return _file_scenario(key)
    if seed is None:
        raise HTTPException(status_code=422, detail="scenario 'random' needs an integer seed "
                            f"between 0 and {SEED_MAX}, for example ?scenario=random&seed=2026")
    return _random_scenario(seed)


def build_log(sc, entries=None):
    """The scenario's bundled log plus the entries the page sent. A bad entry is a 422."""
    try:
        return rules.make_log(sc["log"], entries)
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


SeedParam = Query(None, ge=0, le=SEED_MAX, description="integer, required for scenario 'random'")


@app.get("/scenarios")
async def get_scenarios():
    """The test scenarios the page offers in its dropdown."""
    return [{"key": k, "label": v["label"], "description": v["description"]}
            for k, v in SCENARIOS.items()]


@app.get("/log")
async def get_log(scenario: str = "mixed", seed: Optional[int] = SeedParam):
    """The scenario's bundled maintenance log, so the page can seed its local log."""
    return json_safe(get_scenario(scenario, seed)["log"].to_dict("records"))


DATA_FILES = {"fridge_telemetry": "telemetry", "generator_tests": "tests", "maintenance_log": "log"}


@app.get("/data/{name}.csv")
async def get_data_csv(name: str, scenario: str = "mixed", seed: Optional[int] = SeedParam):
    """Download one of the scenario's input CSVs: the bundled file as-is, or the random week as generated."""
    if name not in DATA_FILES:
        raise HTTPException(status_code=404, detail=f"unknown file '{name}'; use one of: {', '.join(DATA_FILES)}")
    sc = get_scenario(scenario, seed)
    tag = scenario + (f"_seed{seed}" if scenario == "random" else "")
    headers = {"Content-Disposition": f'attachment; filename="{tag}_{name}.csv"'}
    if scenario != "random":
        return FileResponse(DATA_DIR / SCENARIOS[scenario]["folder"] / f"{name}.csv",
                            media_type="text/csv", headers=headers)
    text = sc[DATA_FILES[name]].to_csv(index=False, lineterminator="\n")
    return Response(content=text, media_type="text/csv", headers=headers)


@app.get("/fridges/{unit_id}")
def get_fridge(unit_id: str, as_of: Optional[str] = None, scenario: str = "mixed",
               seed: Optional[int] = SeedParam):
    """qc.fridge_summary for one unit, plus alerts and status (bundled log only)."""
    sc = get_scenario(scenario, seed)
    if unit_id not in set(sc["telemetry"]["unit_id"]):
        raise HTTPException(status_code=404, detail=f"unknown fridge {unit_id}")
    r = run(rules.fridge_status, sc["telemetry"], unit_id, build_log(sc), as_of)
    return {**r.pop("summary"), **r}


@app.get("/generators/{gen_id}")
def get_generator(gen_id: str, as_of: Optional[str] = None, scenario: str = "mixed",
                  seed: Optional[int] = SeedParam):
    """qc.generator_summary for one generator, plus alerts and status (bundled log only)."""
    sc = get_scenario(scenario, seed)
    if gen_id not in set(sc["tests"]["generator_id"]):
        raise HTTPException(status_code=404, detail=f"unknown generator {gen_id}")
    now = as_of or sc["telemetry"]["timestamp"].max()
    r = run(rules.generator_status, sc["tests"], gen_id, build_log(sc), now)
    return {**r.pop("summary"), **r}


def attention(sc, entries, as_of):
    """Attention list of a scenario; the random one also carries its answer key."""
    result = run(rules.attention_list, sc["telemetry"], sc["tests"], build_log(sc, entries), as_of)
    if "answer_key" in sc:
        result["answer_key"] = sc["answer_key"]
    return result


@app.get("/attention")
def get_attention(as_of: Optional[str] = None, scenario: str = "mixed",
                  seed: Optional[int] = SeedParam):
    """Attention list for all units on the bundled log."""
    return attention(get_scenario(scenario, seed), None, as_of)


@app.post("/attention")
def post_attention(body: AttentionRequest, scenario: str = "mixed", seed: Optional[int] = SeedParam):
    """Attention list on the bundled log plus the entries sent in the body.
    scenario and seed may be in the body or in the query; the body wins."""
    sc = get_scenario(body.scenario or scenario, body.seed if body.seed is not None else seed)
    return attention(sc, body.log, body.as_of)


# The built React page (frontend/ -> app/static/). Mounted LAST so every API route above wins.
STATIC_DIR = Path(__file__).resolve().parent / "static"
if STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="page")

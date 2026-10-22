"""FastAPI app for the blood bank monitor. Thin: parse input, call qc / rules, return JSON.

The API stores nothing: the page sends its maintenance log with each request (SPEC section 6).
"""
import hashlib
import io
import math
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query, Request
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
UPLOAD_MAX_BYTES = 10 * 1024 * 1024     # POST /upload refuses a bigger file
UPLOAD_KEEP = 8                         # uploads kept in memory (the least recently used is dropped)

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
    "upload": {"label": "Scenario 5: Upload your own data", "folder": None,
               "description": "Upload one CSV in the combined format (download the template or any "
                              "dataset to start)"},
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
    optional scenario (and seed for "random", upload_id for "upload"); the same names also work as
    query parameters."""
    log: List[dict] = []
    as_of: Optional[str] = None
    scenario: Optional[str] = None
    seed: Optional[int] = Field(None, ge=0, le=SEED_MAX)
    upload_id: Optional[str] = None


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


# ---------------------------------------------------------------------------
# The combined sheet: the three input files as ONE csv (SPEC section 3, data/README.md)
# ---------------------------------------------------------------------------
SHEET_COLUMNS = ["record_type", "equipment_id", "timestamp", "temp_c", "door_open", "power_on",
                 "compressor_duty", "test_type", "started", "seconds_to_start", "battery_v",
                 "fuel_pct", "output_stable", "service_type", "technician", "notes"]
# record_type -> (columns its rows need, of those the ones that must not be blank)
TYPE_COLUMNS = {
    "reading": (["temp_c", "door_open", "power_on", "compressor_duty"], []),
    "generator_test": (["test_type", "started", "battery_v", "fuel_pct"], ["test_type", "started"]),
    "service": (["service_type"], ["service_type"]),
}
TYPE_NUMBERS = {   # record_type -> numeric columns of that type
    "reading": ["temp_c", "door_open", "power_on", "compressor_duty"],
    "generator_test": ["started", "seconds_to_start", "battery_v", "fuel_pct", "output_stable"],
    "service": [],
}
ZERO_ONE = ["door_open", "power_on", "started", "output_stable"]
TEST_COLS = ["generator_id", "test_date", "test_type", "started", "seconds_to_start", "battery_v",
             "fuel_pct", "output_stable"]       # the original column order of generator_tests.csv
MIN_DAYS = 14


def _text(series):
    """A column as text for the sheet: blank for missing, whole numbers without '.0'."""
    def one(v):
        if pd.isna(v):
            return ""
        if isinstance(v, (float, np.floating)):
            return str(int(v)) if float(v).is_integer() else repr(float(v))
        return str(v)
    return series.map(one).astype(object)


def to_combined(telemetry, tests, log):
    """The three input frames -> one frame in the combined-sheet format (all text)."""
    parts = []
    for kind, df, names in (("reading", telemetry, {"unit_id": "equipment_id"}),
                            ("generator_test", tests, {"generator_id": "equipment_id", "test_date": "timestamp"}),
                            ("service", log, {"service_date": "timestamp"})):
        part = pd.DataFrame({names.get(c, c): _text(df[c]) for c in df.columns})
        part.insert(0, "record_type", kind)
        parts.append(part)
    sheet = pd.concat(parts, ignore_index=True).reindex(columns=SHEET_COLUMNS).fillna("")
    return sheet.astype(object)


def from_combined(sheet):
    """A combined-sheet frame (text, every column present) -> (telemetry, tests, log) with the
    original column names the rules expect. Numbers become numbers; blanks become NaN (log: '')."""
    def numbers(rows, cols):
        return {c: pd.to_numeric(rows[c].replace("", np.nan)) for c in cols}
    rows = sheet[sheet["record_type"] == "reading"]
    telemetry = pd.DataFrame({"unit_id": rows["equipment_id"], "timestamp": rows["timestamp"],
                              **numbers(rows, TYPE_NUMBERS["reading"])}).reset_index(drop=True)
    rows = sheet[sheet["record_type"] == "generator_test"]
    tests = pd.DataFrame({"generator_id": rows["equipment_id"], "test_date": rows["timestamp"],
                          "test_type": rows["test_type"],
                          **numbers(rows, TYPE_NUMBERS["generator_test"])})[TEST_COLS].reset_index(drop=True)
    rows = sheet[sheet["record_type"] == "service"]
    log = pd.DataFrame({"equipment_id": rows["equipment_id"], "service_date": rows["timestamp"],
                        "service_type": rows["service_type"], "technician": rows["technician"],
                        "notes": rows["notes"]}).reset_index(drop=True)
    return telemetry, tests, log


def check_sheet(sheet):
    """Validate an uploaded sheet (a text frame). Raises ValueError with a message naming the row
    (spreadsheet row: the header is row 1) and the column. Adds any optional column that is missing."""
    for col in ("record_type", "equipment_id", "timestamp"):
        if col not in sheet.columns:
            raise ValueError(f"missing column '{col}': the first row must name the columns "
                             f"({', '.join(SHEET_COLUMNS)})")
    sheet["record_type"] = sheet["record_type"].str.lower()
    for i, kind in sheet["record_type"].items():
        if kind not in TYPE_COLUMNS:
            raise ValueError(f"row {i + 2}: unknown record_type '{kind}' "
                             "(use reading, generator_test or service)")
    present = set(sheet["record_type"])
    if "reading" not in present:
        raise ValueError("no reading rows: the file needs fridge readings (record_type 'reading')")
    for kind, (needed, _) in TYPE_COLUMNS.items():
        for col in needed:
            if kind in present and col not in sheet.columns:
                raise ValueError(f"missing column '{col}' (needed by {kind} rows)")
    for col in SHEET_COLUMNS:
        if col not in sheet.columns:
            sheet[col] = ""
    for i, row in sheet.iterrows():
        for col in ("equipment_id", "timestamp"):
            if not row[col]:
                raise ValueError(f"row {i + 2}: {col} is blank")
        if any(ch in row["equipment_id"] for ch in " :"):
            raise ValueError(f"row {i + 2}: equipment_id '{row['equipment_id']}' must not contain "
                             "spaces or ':'")
        for col in TYPE_COLUMNS[row["record_type"]][1]:
            if not row[col]:
                raise ValueError(f"row {i + 2}: {col} is blank ({row['record_type']} rows need it)")
        for col in TYPE_NUMBERS[row["record_type"]]:
            if row[col]:
                try:
                    value = float(row[col])
                except ValueError:
                    raise ValueError(f"row {i + 2}: {col} '{row[col]}' is not a number")
                if col in ZERO_ONE and value not in (0.0, 1.0):
                    raise ValueError(f"row {i + 2}: {col} '{row[col]}' must be 0 or 1")
    stamps = {}
    for kind in present:
        rows = sheet[sheet["record_type"] == kind]
        if kind == "service":     # each date is read on its own, like rules.make_log does
            parsed = pd.Series([pd.to_datetime(t, errors="coerce") for t in rows["timestamp"]], index=rows.index)
        else:                     # one column, one format, like the rules read it
            parsed = pd.to_datetime(rows["timestamp"], errors="coerce")
        for i in parsed.index[parsed.isna()]:
            raise ValueError(f"row {i + 2}: timestamp '{rows.loc[i, 'timestamp']}' is not a date/time "
                             "in one format like 2026-10-20T06:00:00 (the same format on every row)")
        stamps[kind] = parsed
    both = set(sheet.loc[sheet["record_type"] == "reading", "equipment_id"]) & \
        set(sheet.loc[sheet["record_type"] == "generator_test", "equipment_id"])
    if both:
        raise ValueError(f"{sorted(both)[0]} has both readings and generator tests: use different ids")
    readings = sheet[sheet["record_type"] == "reading"].assign(when=stamps["reading"])
    for unit, rows in readings.groupby("equipment_id"):
        days = (rows["when"].max() - rows["when"].min()) / pd.Timedelta(days=1) + 1 / 96   # + one 15-min reading
        if days < MIN_DAYS - 1e-9:
            raise ValueError(f"{unit} has {days:.1f} days of readings; the method needs {MIN_DAYS}: "
                             "a baseline week and a latest week")


_uploads = OrderedDict()    # upload id -> parsed scenario; only the 8 most recently used, in memory only


def store_upload(raw):
    """Parse and check the uploaded bytes, keep the scenario in memory, return the summary dict."""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=422, detail="the file is not UTF-8 text: save it as CSV (UTF-8)")
    try:
        sheet = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)
    except pd.errors.EmptyDataError:
        raise HTTPException(status_code=422, detail="the file is empty: it needs a header row and data rows")
    except pd.errors.ParserError as err:
        raise HTTPException(status_code=422, detail=f"the file is not valid CSV: {err}")
    sheet.columns = [c.strip() for c in sheet.columns]
    sheet = sheet.apply(lambda col: col.str.strip())
    try:
        check_sheet(sheet)
        telemetry, tests, log = from_combined(sheet)
        result = rules.attention_list(telemetry, tests, rules.make_log(log), None)   # it must be analysable
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err))
    upload_id = hashlib.sha256(raw).hexdigest()[:16]
    _uploads[upload_id] = {"telemetry": telemetry, "tests": tests, "log": log}
    _uploads.move_to_end(upload_id)
    while len(_uploads) > UPLOAD_KEEP:
        _uploads.popitem(last=False)
    counts = sheet["record_type"].value_counts()
    return {"id": upload_id, "units": sorted(({"id": u["id"], "kind": u["kind"]} for u in result["units"]), key=lambda u: u["id"]),
            "counts": {k: int(counts.get(k, 0)) for k in TYPE_COLUMNS}, "as_of": result["as_of"]}


def get_scenario(key, seed=None, upload_id=None):
    """The data of a scenario. Unknown scenario: 404. Random without a seed, upload without an id: 422."""
    if key not in SCENARIOS:
        raise HTTPException(status_code=404, detail=f"unknown scenario '{key}'; "
                            f"use one of: {', '.join(SCENARIOS)}")
    if key == "upload":
        if not upload_id:
            raise HTTPException(status_code=422, detail="scenario 'upload' needs an upload_id: "
                                "POST your CSV to /upload first and use the id it returns")
        if upload_id not in _uploads:
            raise HTTPException(status_code=404, detail="upload not found (the server may have "
                                "restarted): upload the file again")
        _uploads.move_to_end(upload_id)
        return _uploads[upload_id]
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
UploadParam = Query(None, description="id returned by POST /upload, required for scenario 'upload'")


@app.get("/scenarios")
async def get_scenarios():
    """The test scenarios the page offers in its dropdown."""
    return [{"key": k, "label": v["label"], "description": v["description"]}
            for k, v in SCENARIOS.items()]


@app.get("/log")
async def get_log(scenario: str = "mixed", seed: Optional[int] = SeedParam,
                  upload_id: Optional[str] = UploadParam):
    """The scenario's bundled maintenance log, so the page can seed its local log."""
    return json_safe(get_scenario(scenario, seed, upload_id)["log"].to_dict("records"))


DATA_FILES = {"fridge_telemetry": "telemetry", "generator_tests": "tests", "maintenance_log": "log"}


def csv_response(text, filename):
    return Response(content=text, media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.post("/upload")
async def post_upload(request: Request):
    """Upload your own data: the body is the combined-sheet CSV text itself (for example
    curl --data-binary @sheet.csv -H "Content-Type: text/csv"). Up to 10 MB. The parsed data is kept
    in memory only (the 8 latest uploads); use the returned id as upload_id with scenario=upload."""
    raw = await request.body()
    if len(raw) > UPLOAD_MAX_BYTES:
        raise HTTPException(status_code=413, detail="the file is over 10 MB")
    return store_upload(raw)


# These two come before /data/{name}.csv, which would otherwise catch them.
@app.get("/data/all_data.csv")
async def get_all_data(scenario: str = "mixed", seed: Optional[int] = SeedParam,
                       upload_id: Optional[str] = UploadParam):
    """The scenario as ONE combined-sheet CSV (the three input files in one)."""
    sc = get_scenario(scenario, seed, upload_id)
    tag = scenario + (f"_seed{seed}" if scenario == "random" else "")
    text = to_combined(sc["telemetry"], sc["tests"], sc["log"]).to_csv(index=False, lineterminator="\n")
    return csv_response(text, f"{tag}_all_data.csv")


@app.get("/data/template.csv")
async def get_template():
    """The template: the header plus a few example rows of each record type (a format example only)."""
    return FileResponse(DATA_DIR / "template_all_data.csv", media_type="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="template_all_data.csv"'})


@app.get("/data/{name}.csv")
async def get_data_csv(name: str, scenario: str = "mixed", seed: Optional[int] = SeedParam,
                       upload_id: Optional[str] = UploadParam):
    """Download one of the scenario's input CSVs: the bundled file as-is, or the generated data."""
    if name not in DATA_FILES:
        raise HTTPException(status_code=404, detail=f"unknown file '{name}'; use one of: {', '.join(DATA_FILES)}")
    sc = get_scenario(scenario, seed, upload_id)
    tag = scenario + (f"_seed{seed}" if scenario == "random" else "")
    if SCENARIOS[scenario]["folder"] is not None:
        return FileResponse(DATA_DIR / SCENARIOS[scenario]["folder"] / f"{name}.csv",
                            media_type="text/csv",
                            headers={"Content-Disposition": f'attachment; filename="{tag}_{name}.csv"'})
    return csv_response(sc[DATA_FILES[name]].to_csv(index=False, lineterminator="\n"), f"{tag}_{name}.csv")


@app.get("/fridges/{unit_id}")
def get_fridge(unit_id: str, as_of: Optional[str] = None, scenario: str = "mixed",
               seed: Optional[int] = SeedParam, upload_id: Optional[str] = UploadParam):
    """qc.fridge_summary for one unit, plus alerts and status (bundled log only)."""
    sc = get_scenario(scenario, seed, upload_id)
    if unit_id not in set(sc["telemetry"]["unit_id"]):
        raise HTTPException(status_code=404, detail=f"unknown fridge {unit_id}")
    r = run(rules.fridge_status, sc["telemetry"], unit_id, build_log(sc), as_of)
    return {**r.pop("summary"), **r}


@app.get("/generators/{gen_id}")
def get_generator(gen_id: str, as_of: Optional[str] = None, scenario: str = "mixed",
                  seed: Optional[int] = SeedParam, upload_id: Optional[str] = UploadParam):
    """qc.generator_summary for one generator, plus alerts and status (bundled log only)."""
    sc = get_scenario(scenario, seed, upload_id)
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
                  seed: Optional[int] = SeedParam, upload_id: Optional[str] = UploadParam):
    """Attention list for all units on the bundled log."""
    return attention(get_scenario(scenario, seed, upload_id), None, as_of)


@app.post("/attention")
def post_attention(body: AttentionRequest, scenario: str = "mixed", seed: Optional[int] = SeedParam,
                   upload_id: Optional[str] = UploadParam):
    """Attention list on the bundled log plus the entries sent in the body.
    scenario, seed and upload_id may be in the body or in the query; the body wins."""
    sc = get_scenario(body.scenario or scenario, body.seed if body.seed is not None else seed,
                      body.upload_id or upload_id)
    return attention(sc, body.log, body.as_of)


# The built React page (frontend/ -> app/static/). Mounted LAST so every API route above wins.
STATIC_DIR = Path(__file__).resolve().parent / "static"
if STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="page")

"""Tests for app/main.py with FastAPI's TestClient, on the bundled data."""
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import qc
from app.main import app

ROOT = Path(__file__).resolve().parent.parent
client = TestClient(app)


def test_bundled_data_is_identical_to_data_folder():
    """Drift guard: app/data/ holds byte-identical copies of every scenario folder in data/."""
    for folder in ("", "all_healthy", "blackout_day"):
        for name in ("fridge_telemetry.csv", "generator_tests.csv", "maintenance_log.csv"):
            mine, theirs = ROOT / "app" / "data" / folder / name, ROOT / "data" / folder / name
            assert theirs.exists() and mine.read_bytes() == theirs.read_bytes(), (folder, name)


def test_health_and_root():
    assert client.get("/health").status_code == 200
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/fridges/FR-01").json()["unit_id"] == "FR-01"


def test_root_serves_the_built_page():
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert '<div id="root">' in r.text
    assert client.get("/docs").status_code == 200


def test_fridge_matches_qc_directly():
    df = pd.read_csv(ROOT / "data" / "fridge_telemetry.csv")
    want = qc.fridge_summary(df, "FR-01")
    body = client.get("/fridges/FR-01").json()
    for key in ("center", "sigma", "ucl", "lcl"):
        assert body["imr"][key] == pytest.approx(want["imr"][key], abs=1e-9)
    for key in ("cpk", "ppk", "cp", "pp"):
        assert body["capability"][key] == pytest.approx(want["capability"][key], abs=1e-9)
    assert body["status"] == "WATCH"


def test_generator_b_dead_time():
    body = client.get("/generators/GEN-B").json()
    assert body["dead_time"]["u"] == pytest.approx(0.037494, abs=1e-6)
    assert body["dead_time"]["u_upper"] == pytest.approx(0.126914, abs=1e-6)
    assert body["status"] in ("MAINTENANCE", "URGENT")


def test_unknown_ids_are_404():
    assert client.get("/fridges/FR-99").status_code == 404
    assert client.get("/generators/GEN-Z").status_code == 404


def test_log_endpoint_returns_bundled_log():
    rows = client.get("/log").json()
    assert len(rows) == 17 and rows[0]["equipment_id"] == "FR-01"


def test_attention_order_and_statuses():
    units = client.get("/attention").json()["units"]
    got = {u["id"]: u["status"] for u in units}
    assert len(units) == 6
    assert got["FR-01"] == "WATCH" and got["GEN-A"] == "OK"
    assert got["FR-02"] == "URGENT" and got["FR-04"] == "MAINTENANCE"
    tiers = ["OK", "WATCH", "MAINTENANCE", "URGENT", "CRITICAL"]
    ranks = [tiers.index(u["status"]) for u in units]
    assert ranks == sorted(ranks, reverse=True)


def test_post_acknowledge_changes_urgent_to_not_urgent():
    before = {u["id"]: u for u in client.get("/attention").json()["units"]}
    assert before["FR-02"]["status"] == "URGENT"
    acks = [{"equipment_id": "FR-02", "service_date": "2026-10-20", "service_type": "acknowledge",
             "technician": "Sam", "notes": a["id"]} for a in before["FR-02"]["alerts"]]
    after = {u["id"]: u for u in client.post("/attention", json={"log": acks}).json()["units"]}
    assert after["FR-02"]["status"] == "MAINTENANCE"


def test_post_checklist_yes_clears_interval_alert():
    yes = {"equipment_id": "FR-02", "service_date": "2026-10-20", "service_type": "coil cleaning",
           "technician": "Sam", "notes": "checklist yes"}
    body = client.post("/attention", json={"log": [yes]}).json()
    fr02 = next(u for u in body["units"] if u["id"] == "FR-02")
    assert "FR-02:coil cleaning overdue" not in [a["id"] for a in fr02["alerts"]]


def test_post_bad_entry_is_422():
    r = client.post("/attention", json={"log": [{"equipment_id": "FR-02"}]})
    assert r.status_code == 422


def test_post_maintenance_complete_on_fr02():
    row = {"equipment_id": "FR-02", "service_date": "2026-10-20T07:00:00",
           "service_type": "maintenance complete", "technician": "Sam", "notes": ""}
    body = client.post("/attention", json={"log": [row]}).json()
    fr02 = next(u for u in body["units"] if u["id"] == "FR-02")
    assert fr02["status"] == "TASKS COMPLETE" and fr02["alerts"] == []
    assert fr02["resolved_by"] == "Sam" and fr02["resolved_alerts"]
    others = [u for u in body["units"] if u["id"] != "FR-02"]
    assert all(u["resolved_alerts"] == [] for u in others)


def test_post_task_complete_on_coil_alert_resets_days_since_in_the_response():
    row = {"equipment_id": "FR-02", "service_date": "2026-10-21T09:00:00", "service_type": "task complete",
           "technician": "Sam", "notes": "FR-02:coil cleaning overdue: Clean the condenser coils and log it."}
    body = client.post("/attention", json={"log": [row]}).json()
    fr02 = next(u for u in body["units"] if u["id"] == "FR-02")
    assert fr02["days_since"]["coil cleaning"] == 0
    assert [a["id"] for a in fr02["resolved_alerts"]] == ["FR-02:coil cleaning overdue"]
    assert "FR-02:control chart drift" in [a["id"] for a in fr02["alerts"]]


def test_dataset_csv_download_is_the_bundled_file_or_the_generated_week():
    r = client.get("/data/fridge_telemetry.csv")
    assert r.status_code == 200 and r.content == open("data/fridge_telemetry.csv", "rb").read()
    assert 'filename="mixed_fridge_telemetry.csv"' in r.headers["content-disposition"]
    r = client.get("/data/generator_tests.csv?scenario=blackout")
    assert r.content == open("data/blackout_day/generator_tests.csv", "rb").read()
    a = client.get("/data/fridge_telemetry.csv?scenario=random&seed=2026").text
    b = client.get("/data/fridge_telemetry.csv?scenario=random&seed=2026").text
    assert a == b and a.startswith("unit_id,timestamp,temp_c")     # same seed, same week


def test_dataset_csv_download_errors():
    assert client.get("/data/nope.csv").status_code == 404
    assert client.get("/data/fridge_telemetry.csv?scenario=nope").status_code == 404
    assert client.get("/data/fridge_telemetry.csv?scenario=random").status_code == 422

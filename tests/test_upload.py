"""Tests for the combined sheet: download, template, upload (POST /upload) and scenario=upload."""
import io
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import main
from app.main import app, from_combined, get_scenario, to_combined

ROOT = Path(__file__).resolve().parent.parent
client = TestClient(app)
SCENARIOS = [("mixed", {}), ("healthy", {}), ("blackout", {}), ("random", {"seed": 2026})]


def sheet_text(scenario, **params):
    r = client.get("/data/all_data.csv", params={"scenario": scenario, **params})
    assert r.status_code == 200
    return r.text


def upload(text):
    return client.post("/upload", content=text.encode("utf-8"), headers={"Content-Type": "text/csv"})


def attention(scenario, **params):
    body = client.get("/attention", params={"scenario": scenario, **params}).json()
    body.pop("answer_key", None)
    return body


def as_frame(text):
    return pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)


def status_of(body, unit):
    return next(u["status"] for u in body["units"] if u["id"] == unit)


@pytest.mark.parametrize("key,params", SCENARIOS)
def test_round_trip_gives_the_same_analysis_inputs(key, params):
    sc = get_scenario(key, params.get("seed"))
    back = from_combined(to_combined(sc["telemetry"], sc["tests"], sc["log"]))
    for before, after in zip((sc["telemetry"], sc["tests"], sc["log"]), back):
        pd.testing.assert_frame_equal(before, after, check_dtype=False)


@pytest.mark.parametrize("key,params", SCENARIOS)
def test_download_then_upload_reproduces_the_attention_list(key, params):
    r = client.get("/data/all_data.csv", params={"scenario": key, **params})
    assert r.headers["content-disposition"] == \
        f'attachment; filename="{key}{"_seed2026" if key == "random" else ""}_all_data.csv"'
    assert list(as_frame(r.text).columns) == main.SHEET_COLUMNS
    up = upload(r.text)
    assert up.status_code == 200, up.text
    assert attention("upload", upload_id=up.json()["id"]) == attention(key, **params)


def test_upload_summary():
    body = upload(sheet_text("mixed")).json()
    assert [u["id"] for u in body["units"] if u["kind"] == "fridge"] == ["FR-01", "FR-02", "FR-03", "FR-04"]
    assert {u["id"] for u in body["units"] if u["kind"] == "generator"} == {"GEN-A", "GEN-B"}
    assert body["counts"]["reading"] == 4 * 1344
    assert body["as_of"] == "2026-10-20T06:00:00"


def test_old_per_file_downloads_still_work():
    r = client.get("/data/fridge_telemetry.csv", params={"scenario": "mixed"})
    assert r.status_code == 200 and r.content == (ROOT / "data" / "fridge_telemetry.csv").read_bytes()


def test_scenarios_list_option_5():
    item = [s for s in client.get("/scenarios").json() if s["key"] == "upload"][0]
    assert item["label"] == "Scenario 5: Upload your own data"
    assert item["description"] == ("Upload one CSV in the combined format (download the template or "
                                   "any dataset to start)")


def test_template_download_is_valid_and_has_every_record_type():
    r = client.get("/data/template.csv")
    assert r.status_code == 200
    assert r.content == (ROOT / "data" / "template_all_data.csv").read_bytes()
    assert (ROOT / "app" / "data" / "template_all_data.csv").read_bytes() == r.content
    frame = as_frame(r.text)
    assert list(frame.columns) == main.SHEET_COLUMNS
    assert set(frame["record_type"]) == {"reading", "generator_test", "service"}
    assert frame["equipment_id"][frame["record_type"] == "reading"].nunique() == 2


def test_uploading_the_template_gives_the_14_day_error():
    r = upload(client.get("/data/template.csv").text)
    assert r.status_code == 422
    assert r.json()["detail"] == ("FR-A has 0.0 days of readings; the method needs 14: "
                                  "a baseline week and a latest week")


def test_custom_unit_names_keep_their_statuses():
    text = sheet_text("mixed").replace("FR-01", "Fridge-7").replace("GEN-A", "Backup-1")
    up = upload(text)
    assert up.status_code == 200, up.text
    got = attention("upload", upload_id=up.json()["id"])
    want = attention("mixed")
    assert status_of(got, "Fridge-7") == status_of(want, "FR-01")
    assert status_of(got, "Backup-1") == status_of(want, "GEN-A")
    assert "FR-01" not in [u["id"] for u in got["units"]]
    assert client.get("/fridges/Fridge-7", params={"scenario": "upload", "upload_id": up.json()["id"]}).status_code == 200


def test_edited_data_makes_the_fridge_critical():
    frame = as_frame(sheet_text("healthy"))
    last_day = (frame["record_type"] == "reading") & (frame["equipment_id"] == "FR-01") & \
        (frame["timestamp"] > "2026-10-19T06:00:00")
    assert last_day.sum() == 96
    assert status_of(attention("healthy"), "FR-01") == "OK"
    frame.loc[last_day, "temp_c"] = "7.0"
    up = upload(frame.to_csv(index=False))
    assert up.status_code == 200, up.text
    body = attention("upload", upload_id=up.json()["id"])
    assert status_of(body, "FR-01") == "CRITICAL"
    assert status_of(body, "FR-02") == "OK"


def test_fridges_only_sheet_works_with_no_generators():
    frame = as_frame(sheet_text("mixed"))
    frame = frame[frame["record_type"] == "reading"]
    up = upload(frame.to_csv(index=False))
    assert up.status_code == 200, up.text
    assert all(u["kind"] == "fridge" for u in up.json()["units"])
    body = attention("upload", upload_id=up.json()["id"])
    assert len(body["units"]) == 4 and all(u["kind"] == "fridge" for u in body["units"])


def test_six_fridges_with_odd_names():
    frame = as_frame(sheet_text("healthy"))
    frame = frame[frame["record_type"] == "reading"]
    extra = frame[frame["equipment_id"].isin(["FR-01", "FR-02"])].copy()
    extra["equipment_id"] = extra["equipment_id"].map({"FR-01": "Cold-room-1", "FR-02": "Fridge_7b"})
    up = upload(pd.concat([frame, extra]).to_csv(index=False))
    assert up.status_code == 200, up.text
    assert len(up.json()["units"]) == 6


def broken(column):
    """The mixed sheet (every 12th row, still 14 days) without one column, as upload text."""
    frame = as_frame(sheet_text("mixed")).iloc[::12].reset_index(drop=True)
    return frame.drop(columns=column).to_csv(index=False)


def test_error_missing_column():
    r = upload(broken("temp_c"))
    assert (r.status_code, r.json()["detail"]) == (422, "missing column 'temp_c' (needed by reading rows)")


def test_error_missing_required_column():
    r = upload(broken("equipment_id"))
    assert r.status_code == 422 and r.json()["detail"].startswith("missing column 'equipment_id'")


def test_error_unknown_record_type():
    frame = as_frame(sheet_text("mixed")).iloc[::12].reset_index(drop=True)
    frame.loc[3, "record_type"] = "foo"
    r = upload(frame.to_csv(index=False))
    assert (r.status_code, r.json()["detail"]) == (
        422, "row 5: unknown record_type 'foo' (use reading, generator_test or service)")


def test_error_bad_number_and_bad_timestamp():
    frame = as_frame(sheet_text("mixed")).iloc[::12].reset_index(drop=True)
    bad = frame.copy()
    bad.loc[4, "temp_c"] = "abc"
    r = upload(bad.to_csv(index=False))
    assert (r.status_code, r.json()["detail"]) == (422, "row 6: temp_c 'abc' is not a number")
    bad = frame.copy()
    bad.loc[2, "timestamp"] = "yesterday"
    r = upload(bad.to_csv(index=False))
    assert r.status_code == 422 and r.json()["detail"].startswith("row 4: timestamp 'yesterday' is not a date/time")
    bad = frame.copy()
    bad.loc[1, "door_open"] = "2"
    assert upload(bad.to_csv(index=False)).json()["detail"] == "row 3: door_open '2' must be 0 or 1"


def test_error_only_five_days():
    frame = as_frame(sheet_text("mixed"))
    frame = frame[(frame["record_type"] != "reading") | (frame["timestamp"] < "2026-10-11T06:15:00")]
    r = upload(frame.to_csv(index=False))
    assert r.status_code == 422
    assert r.json()["detail"] == ("FR-01 has 5.0 days of readings; the method needs 14: "
                                  "a baseline week and a latest week")


def test_error_no_readings_empty_file_and_binary():
    frame = as_frame(sheet_text("mixed"))
    r = upload(frame[frame["record_type"] != "reading"].to_csv(index=False))
    assert (r.status_code, r.json()["detail"]) == (
        422, "no reading rows: the file needs fridge readings (record_type 'reading')")
    r = upload("")
    assert (r.status_code, r.json()["detail"]) == (
        422, "the file is empty: it needs a header row and data rows")
    r = client.post("/upload", content=b"\xff\xfe\x00bad")
    assert r.status_code == 422


def test_error_too_big():
    r = client.post("/upload", content=b"x" * (main.UPLOAD_MAX_BYTES + 1))
    assert (r.status_code, r.json()["detail"]) == (413, "the file is over 10 MB")


def test_unknown_upload_id_is_404_everywhere():
    want = "upload not found (the server may have restarted): upload the file again"
    for url in ("/attention", "/log", "/data/all_data.csv", "/fridges/FR-01"):
        r = client.get(url, params={"scenario": "upload", "upload_id": "nope"})
        assert (r.status_code, r.json()["detail"]) == (404, want), url
    r = client.post("/attention", json={"log": [], "scenario": "upload", "upload_id": "nope"})
    assert r.status_code == 404
    r = client.get("/attention", params={"scenario": "upload"})
    assert r.status_code == 422


def test_post_attention_takes_the_upload_id_in_the_body_and_the_log():
    up = upload(sheet_text("mixed")).json()
    log = [{"equipment_id": "FR-04", "service_date": "2026-10-20T07:00:00", "service_type": "task complete",
            "technician": "Ana", "notes": "FR-04:stuck probe: check"}]
    a = client.post("/attention", json={"log": log, "scenario": "upload", "upload_id": up["id"]}).json()
    b = client.post("/attention?scenario=mixed", json={"log": log}).json()
    a.pop("answer_key", None)
    assert a == b


def test_only_eight_uploads_are_kept():
    ids = []
    for n in range(10):                                   # ten different files (a distinct note each)
        frame = as_frame(sheet_text("healthy"))
        frame = frame[(frame["record_type"] == "reading") & (frame["equipment_id"] == "FR-01")].copy()
        frame.loc[frame.index[0], "temp_c"] = f"4.{n}"
        ids.append(upload(frame.to_csv(index=False)).json()["id"])
    assert client.get("/log", params={"scenario": "upload", "upload_id": ids[-1]}).status_code == 200
    assert client.get("/log", params={"scenario": "upload", "upload_id": ids[0]}).status_code == 404

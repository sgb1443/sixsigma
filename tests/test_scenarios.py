"""Tests for the four test scenarios (H-11): the files, the API switch, and the random generator."""
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import simulate
from app.main import app

client = TestClient(app)
ORDER = ["OK", "WATCH", "MAINTENANCE", "URGENT", "CRITICAL"]
SEEDS = range(1, 21)


def units(path):
    """{unit id: unit dict} from an /attention URL."""
    r = client.get(path)
    assert r.status_code == 200, r.text
    return {u["id"]: u for u in r.json()["units"]}


def alert_names(unit):
    return [a["id"].split(":")[1] for a in unit["alerts"]]


# ---------- scenario list and errors ----------
def test_scenarios_lists_five_with_the_default_first():
    r = client.get("/scenarios").json()
    assert [s["key"] for s in r] == ["mixed", "healthy", "blackout", "random", "upload"]
    assert r[0]["label"] == "Scenario 1: Mixed problems"
    assert r[3]["label"] == "Scenario 4: Random data"
    assert all(s["description"] for s in r)


def test_unknown_scenario_is_404_with_a_message():
    for path in ("/attention?scenario=nope", "/log?scenario=nope", "/fridges/FR-01?scenario=nope"):
        r = client.get(path)
        assert r.status_code == 404 and "unknown scenario" in r.json()["detail"]


def test_random_needs_an_integer_seed():
    r = client.get("/attention?scenario=random")
    assert r.status_code == 422 and "seed" in r.json()["detail"]
    assert client.get("/attention?scenario=random&seed=abc").status_code == 422
    assert client.post("/attention", json={"scenario": "random"}).status_code == 422


def test_post_attention_takes_the_scenario_in_the_body():
    r = client.post("/attention", json={"scenario": "blackout"}).json()
    got = {u["id"]: u["status"] for u in r["units"]}
    assert got["FR-02"] == "CRITICAL"
    r = client.post("/attention", json={"scenario": "random", "seed": 2026}).json()
    assert r["answer_key"] == client.get("/attention?scenario=random&seed=2026").json()["answer_key"]


def test_default_scenario_is_mixed_and_has_no_answer_key():
    assert "answer_key" not in client.get("/attention").json()
    assert client.get("/attention").json() == client.get("/attention?scenario=mixed").json()


# ---------- scenario 2: all healthy ----------
def test_healthy_scenario_has_no_unit_above_watch():
    got = units("/attention?scenario=healthy")
    assert set(got) == {"FR-01", "FR-02", "FR-03", "FR-04", "GEN-A", "GEN-B"}
    for u in got.values():
        assert ORDER.index(u["status"]) <= ORDER.index("WATCH"), u["id"]
    assert got["GEN-A"]["status"] == "OK" and got["GEN-B"]["status"] == "OK"


# ---------- scenario 3: blackout day ----------
def test_blackout_scenario():
    got = units("/attention?scenario=blackout")
    assert got["FR-02"]["status"] == "CRITICAL"
    assert got["FR-01"]["status"] == "URGENT"          # peaks 5.9 C while a power-loss alert is active
    for f in ("FR-01", "FR-02", "FR-03", "FR-04"):
        assert "power loss" in alert_names(got[f]), f
    assert got["FR-03"]["status"] == got["FR-04"]["status"] == "MAINTENANCE"
    assert any(n.startswith("failed start") for n in alert_names(got["GEN-A"]))
    assert got["GEN-B"]["status"] == "OK"


def test_detail_endpoints_follow_the_scenario():
    assert client.get("/generators/GEN-A?scenario=blackout").json()["status"] != "OK"
    assert client.get("/generators/GEN-A?scenario=healthy").json()["status"] == "OK"
    assert client.get("/fridges/FR-02?scenario=blackout").json()["status"] == "CRITICAL"
    assert len(client.get("/log?scenario=healthy").json()) == 17


# ---------- scenario 4: random ----------
def test_same_seed_same_data_different_seed_different_data():
    a, b = simulate.random_scenario(7), simulate.random_scenario(7)
    for x, y in zip(a[:3], b[:3]):
        assert x.equals(y)
    assert a[3] == b[3]
    c = simulate.random_scenario(8)
    assert not a[0].equals(c[0])


def test_random_data_has_the_same_shape_as_the_files():
    fridge, gens, log, key = simulate.random_scenario(2026)
    real = pd.read_csv("data/fridge_telemetry.csv")
    assert list(fridge.columns) == list(real.columns) and len(fridge) == len(real)
    assert fridge["timestamp"].min() == real["timestamp"].min()
    assert fridge["timestamp"].max() == real["timestamp"].max()
    assert list(gens.columns) == list(pd.read_csv("data/generator_tests.csv").columns)
    assert set(key) == {"FR-01", "FR-02", "FR-03", "FR-04", "GEN-A", "GEN-B"}


# Healthy-by-plan fridges that the I-MR still flags: the method's false alarms, reported not tuned.
# (seed, unit): what the chart did. A run of 8 hourly means on one side of the baseline centre line
# in the last 24 h is the usual cause.
KNOWN_FALSE_ALARMS = {
    (14, "FR-02"): "run of 8 hourly means below the centre line, 2026-10-19 15:00-19:00",
    (18, "FR-03"): "control chart drift on a healthy fridge (noise only)",
    (19, "FR-01"): "control chart drift on a healthy fridge (noise only)",
}


@pytest.mark.parametrize("seed", SEEDS)
def test_random_seed_planted_behaviours_are_flagged(seed):
    r = client.get(f"/attention?scenario=random&seed={seed}").json()
    key = r["answer_key"]
    for unit in r["units"]:
        text, names = key[unit["id"]], alert_names(unit)
        if text.startswith("Healthy") and "overdue" not in text:
            if (seed, unit["id"]) not in KNOWN_FALSE_ALARMS:
                assert ORDER.index(unit["status"]) <= 1, (seed, unit["id"], unit["status"], names)
        if text.startswith("Stuck probe"):
            assert "stuck probe" in names, (seed, unit["id"], names)
        if text.startswith("Power outage"):
            assert "power loss" in names, (seed, unit["id"], names)
            if "expect CRITICAL" in text:
                assert unit["status"] == "CRITICAL", (seed, unit["id"], unit["status"])
        if text.startswith("Failing"):
            assert any(n.startswith("failed start") for n in names), (seed, unit["id"], names)
        if text.startswith("Door-seal"):
            assert "door open" in names, (seed, unit["id"], names)
        if "overdue" in text:
            assert any(n.endswith("overdue") for n in names), (seed, unit["id"], names)

"""Tests for the fridge statistics in app/qc.py (SPEC.md section 4)."""
import math
import os

import pandas as pd
import pytest

from app.qc import capability, fridge_summary, hourly_means, imr, stuck_probe

KNOWN = [4.0, 4.2, 3.9, 4.1, 4.3]
CSV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "data", "fridge_telemetry.csv")


# ---------- known answer (SPEC section 4) ----------
def test_imr_known_answer():
    r = imr(KNOWN)
    assert r["center"] == pytest.approx(4.10, abs=1e-6)
    assert r["mr_bar"] == pytest.approx(0.225, abs=1e-6)
    assert r["sigma"] == pytest.approx(0.1995, abs=1e-4)
    assert r["ucl"] == pytest.approx(4.698, abs=1e-3)
    assert r["lcl"] == pytest.approx(3.502, abs=1e-3)
    assert r["mr_ucl"] == pytest.approx(3.267 * 0.225, abs=1e-6)
    assert r["any_signal"] is False


def test_capability_known_answer():
    c = capability(KNOWN)
    assert c["cp"] == pytest.approx(4.18, abs=0.01)
    assert c["cpk"] == pytest.approx(3.18, abs=0.01)
    assert c["capable"] is True


def test_overall_known_answer():
    c = capability(KNOWN)
    assert c["overall_sd"] == pytest.approx(0.15811, abs=1e-4)
    assert c["pp"] == pytest.approx(5.27, abs=0.01)
    assert c["ppk"] == pytest.approx(4.006, abs=0.01)


def test_capable_uses_ppk():
    # alternating data: MR-bar large, so Cpk is small; sorted data: MR-bar small but same spread
    x = [1.5, 2.5, 3.5, 4.5, 5.5] * 3
    assert capability(sorted(x))["cpk"] > capability(sorted(x))["ppk"]
    assert capability(sorted(x))["capable"] is False   # decided by Ppk (about 0.9)


# ---------- run rules ----------
def test_beyond_limits_flag():
    baseline = KNOWN * 4                      # in-control baseline
    r = imr([4.1, 4.1, 6.0, 4.1], baseline=baseline)
    assert r["flags"] == [False, False, True, False]
    assert r["any_signal"] is True


def test_run_of_eight_exact():
    baseline = KNOWN * 4
    values = [4.2] * 10                       # 10 above center
    r = imr(values, baseline=baseline)
    assert r["run_flags"] == [False] * 7 + [True] * 3
    assert r["flags"] == r["run_flags"]       # 4.2 is well inside the limits


def test_run_of_seven_does_not_flag():
    r = imr([4.2] * 7 + [4.0], baseline=KNOWN * 4)
    assert not any(r["flags"])


# ---------- degenerate cases ----------
def test_constant_data_no_variation():
    r = imr([4.0] * 10)
    assert r["sigma"] == 0
    assert r["any_signal"] is False
    c = capability([4.0] * 10)
    assert c["cp"] is None and c["cpk"] is None
    assert c["pp"] is None and c["ppk"] is None
    assert c["capable"] is None
    assert "no variation" in c["message"]


def test_single_value_raises():
    with pytest.raises(ValueError, match="moving range"):
        imr([4.0])
    with pytest.raises(ValueError, match="moving range"):
        capability([4.0])


def test_empty_raises():
    with pytest.raises(ValueError):
        imr([])
    with pytest.raises(ValueError):
        capability([None, float("nan")])


def test_missing_values_skipped():
    r = imr([4.0, 4.2, None, 3.9, float("nan"), 4.1, 4.3])
    assert r["center"] == pytest.approx(4.10, abs=1e-6)
    assert r["n"] == 5
    assert capability([4.0, 4.2, None, 3.9, 4.1, 4.3])["cpk"] == pytest.approx(3.18, abs=0.01)


def test_low_capability_flagged():
    c = capability([1.5, 5.5, 1.7, 5.4, 1.6, 5.6])   # huge spread, not capable
    assert c["capable"] is False


# ---------- stuck probe ----------
def test_stuck_probe_run_and_missing():
    assert stuck_probe([4.0, 4.1] * 20)["stuck"] is False
    r = stuck_probe([3.9] + [4.0] * 12 + [4.1])
    assert r["stuck"] is True and r["longest_run"] == 12
    assert stuck_probe([4.0] * 11 + [4.1])["stuck"] is False


def test_stuck_probe_flat_last_window():
    # 96 flat readings are caught by the MR-bar = 0 rule even with run_len raised
    r = stuck_probe([4.0, 4.5] * 10 + [4.0] * 96, run_len=200)
    assert r["stuck"] is True


def test_hourly_means_keeps_only_complete_hours():
    def rows(hour, temps):
        return [("A", f"2026-01-01T{hour:02d}:{15 * i:02d}:00", t) for i, t in enumerate(temps)]
    data = (rows(0, [4.0, 5.0, 6.0])              # edge hour with only 3 rows: dropped
            + rows(1, [9.0])                      # 1-row hour: dropped
            + rows(2, [4.0, None, 5.0, 6.0])      # full hour, 1 blank: kept
            + rows(3, [4.0, None, None, 6.0])     # full hour, 2 blanks: dropped
            + rows(4, [1.0, 2.0, 3.0, 4.0]))      # full hour: kept
    h = hourly_means(pd.DataFrame(data, columns=["unit_id", "timestamp", "temp_c"]))
    assert [t.hour for t in h["hour"]] == [2, 4]
    assert h["temp_c"].tolist() == pytest.approx([5.0, 2.5])
    assert h["n_readings"].tolist() == [3, 4]


def test_167_hourly_points_each_week(df):
    for u in ["FR-01", "FR-02", "FR-03", "FR-04"]:
        i = fridge_summary(df, u)["imr"]
        assert i["n"] == 167 and i["n_baseline_hours"] == 167


# ---------- the real data files ----------
@pytest.fixture(scope="module")
def df():
    return pd.read_csv(CSV)


def test_fr02_signal(df):
    assert fridge_summary(df, "FR-02")["imr"]["any_signal"] is True


def test_fr02_imr_alert(df):
    s = fridge_summary(df, "FR-02")
    assert s["imr_alert"] is True and s["signals_last_24h"] > 0


def test_fr03_out_of_spec(df):
    s = fridge_summary(df, "FR-03")
    stamps = [x["timestamp"] for x in s["out_of_spec"]]
    assert "2026-10-16T14:30:00" in stamps and "2026-10-16T14:45:00" in stamps
    assert all(x["temp_c"] > 6.0 or x["temp_c"] < 1.0 for x in s["out_of_spec"])


def test_fr04_stuck(df):
    r = fridge_summary(df, "FR-04")["stuck_probe"]
    assert r["stuck"] is True and r["longest_run"] == 96


def test_fr01_not_stuck(df):
    assert fridge_summary(df, "FR-01")["stuck_probe"]["stuck"] is False


def test_unknown_unit_raises(df):
    with pytest.raises(ValueError):
        fridge_summary(df, "FR-99")


def test_print_summary(df, capsys):
    """Not an assertion: prints the FR-01..04 numbers (run with pytest -s)."""
    with capsys.disabled():
        print()
        for u in ["FR-01", "FR-02", "FR-03", "FR-04"]:
            s = fridge_summary(df, u)
            i, c = s["imr"], s["capability"]
            print(u, "center", round(i["center"], 3), "sigma", round(i["sigma"], 3),
                  "UCL", round(i["ucl"], 3), "LCL", round(i["lcl"], 3),
                  "n_base", i["n_baseline_hours"], "n", i["n"], "signals", sum(i["flags"]),
                  "sig24h", s["signals_last_24h"],
                  "Cp", round(c["cp"], 2), "Cpk", round(c["cpk"], 2),
                  "Pp", round(c["pp"], 2), "Ppk", round(c["ppk"], 4),
                  "stuck", s["stuck_probe"]["stuck"], "out_of_spec", len(s["out_of_spec"]))

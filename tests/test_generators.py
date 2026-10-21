"""Tests for the generator statistics in app/qc.py (SPEC.md section 4)."""
import math
import os

import pandas as pd
import pytest

from app.qc import dead_time, generator_summary, p_chart, poisson_upper

CSV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "data", "generator_tests.csv")


def test_p_chart_known_answer():
    r = p_chart([1, 1, 1, 1] + [0] * 21, [4] * 25)      # 4 failures / 100 tests
    assert r["p_bar"] == pytest.approx(0.04)
    assert r["sigma"][0] == pytest.approx(0.0980, abs=1e-4)
    assert r["ucl"][0] == pytest.approx(0.334, abs=1e-3)
    assert r["lcl"][0] == 0
    assert r["small_n_warning"] is True                  # np-bar = 0.16
    assert r["any_signal"] is False                      # 1 of 4 = 0.25 < 0.334


def test_p_chart_flags_high_month():
    r = p_chart([0] * 9 + [3], [4] * 10)     # p-bar 0.075, UCL 0.47, last p 0.75
    assert r["flags"] == [False] * 9 + [True]


def test_p_chart_degenerate():
    r = p_chart([0, 0], [4, 4])
    assert r["ucl"] is None and r["lcl"] is None
    assert r["message"] == "no failures observed: p-chart limits not defined"
    with pytest.raises(ValueError):
        p_chart([], [])
    with pytest.raises(ValueError):
        p_chart([0, 1], [4, 0])


def test_dead_time_known_answer():
    r = dead_time(1, 1000, 30)                           # lambda = 0.001
    assert r["lambda_per_day"] == pytest.approx(0.001)
    assert r["approx"] == pytest.approx(0.015)
    assert r["u"] == pytest.approx(0.014851, abs=1e-6)
    assert r["flag"] is True
    assert r["t_max_approx"] == pytest.approx(20.0)
    lam, t = r["lambda_per_day"], r["t_max_exact"]
    assert 1 - (1 - math.exp(-lam * t)) / (lam * t) == pytest.approx(0.01, abs=1e-6)


def test_dead_time_degenerate():
    r = dead_time(0, 200, 7)
    assert r["u"] is None and r["lambda_per_day"] == 0
    assert r["message"] == "no failures observed, estimate not available"
    for args in [(1, 0, 7), (1, 100, 0), (1, -5, 7)]:
        with pytest.raises(ValueError):
            dead_time(*args)


@pytest.fixture(scope="module")
def df():
    return pd.read_csv(CSV)


def test_gen_b_october_flagged(df):
    c = generator_summary(df, "GEN-B")["p_chart"]
    i = c["months"].index("2026-10")
    assert c["failures"][i] == 2 and c["flags"][i] is True
    assert c["message"] == "no failures in the baseline: any failure is a signal"
    assert c["baseline_months"] == ["2026-05", "2026-06", "2026-07", "2026-08", "2026-09"]
    assert c["judged"][0] is False and c["notes"][0] == "too few tests to judge"
    assert c["flags"][0] is False
    assert sum(c["flags"]) == 1


def test_poisson_upper_known_answers():
    assert poisson_upper(0) == pytest.approx(3.6889, abs=1e-3)
    assert poisson_upper(1) == pytest.approx(5.5716, abs=1e-3)
    assert poisson_upper(2) == pytest.approx(7.2247, abs=1e-3)
    with pytest.raises(ValueError):
        poisson_upper(-1)


def test_gen_b_uncertainty(df):
    d = generator_summary(df, "GEN-B")["dead_time"]
    assert d["n_failures"] == 2
    assert d["caveat"] == "based on only 2 failures: a rough estimate"
    assert d["lambda_upper"] == pytest.approx(0.039696, abs=1e-5)
    assert d["u_upper"] == pytest.approx(0.126914, abs=1e-5)


def test_dead_time_no_caveat_at_five_failures_and_none_at_zero():
    assert dead_time(5, 500, 7)["caveat"] == ""
    z = dead_time(0, 500, 7)
    assert z["u_upper"] is None and z["lambda_upper"] is None and z["n_failures"] == 0


def test_baseline_zero_any_failure_rule():
    r = p_chart([0, 1, 0], [4, 4, 4], baseline=([0, 0], [4, 4]))
    assert r["message"] == "no failures in the baseline: any failure is a signal"
    assert r["flags"] == [False, True, False] and r["ucl"] is None


def test_baseline_with_failures_judges_latest_month():
    # baseline p-bar = 2/20 = 0.1, n = 4: UCL = 0.1 + 3*sqrt(0.09/4) = 0.55
    r = p_chart([0, 2, 3], [4, 4, 4], baseline=([1, 0, 1, 0, 0], [4] * 5))
    assert r["p_bar"] == pytest.approx(0.1)
    assert r["ucl"][0] == pytest.approx(0.55, abs=1e-3)
    assert r["flags"] == [False, False, True]          # 0.5 inside, 0.75 outside


def test_summary_needs_baseline():
    df1 = pd.DataFrame({"generator_id": ["G"], "test_date": ["2026-01-05"], "started": [1]})
    with pytest.raises(ValueError):
        generator_summary(df1, "G")


def test_gen_a_no_failures(df):
    s = generator_summary(df, "GEN-A")
    assert s["p_chart"]["message"] == "no failures in the baseline: any failure is a signal"
    assert not any(s["p_chart"]["flags"])
    assert s["dead_time"]["message"] == "no failures observed, estimate not available"


def test_unknown_generator(df):
    with pytest.raises(ValueError):
        generator_summary(df, "GEN-Z")


def test_print_generator_summary(df):
    """Not an assertion: prints numbers (run with pytest -s)."""
    for g in ["GEN-A", "GEN-B"]:
        s = generator_summary(df, g)
        c, d = s["p_chart"], s["dead_time"]
        print()
        print(g, "months", c["months"])
        print("  tests", c["tests"], "failures", c["failures"])
        print("  p_bar", c["p_bar"], "np_bar", c["np_bar"], "small_n", c["small_n_warning"])
        print("  judged", c["judged"], "flags", c["flags"], "|", c["message"])
        print("  caveat", d["caveat"], "n_failures", d["n_failures"],
              "lambda_upper", d["lambda_upper"], "u_upper", d["u_upper"])
        print("  days", d["days_in_service"], "lambda", d["lambda_per_day"], "U(7)", d["u"],
              "approx", d["approx"], "Tmax approx", d["t_max_approx"], "exact", d["t_max_exact"])

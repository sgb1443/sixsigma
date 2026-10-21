"""Fridge statistics for the blood bank monitor (SPEC.md section 4).

Plain functions, no web framework. Inputs are lists or pandas objects; outputs are plain dicts.
Missing readings (None / NaN) are skipped everywhere.
"""
import math

import pandas as pd

D2 = 1.128            # d2 for subgroups of size 2 (moving range of 2 points)
D4 = 3.267            # D4 for n = 2: upper limit of the moving-range chart
LSL, USL = 1.0, 6.0   # red cell storage spec, degrees C
CPK_MIN = 1.33        # capable if Cpk >= this


def _clean(values):
    """Return the values as floats, with None and NaN removed."""
    out = []
    for v in values:
        if v is None:
            continue
        v = float(v)
        if not math.isnan(v):
            out.append(v)
    return out


def _mr_bar(x):
    """Mean moving range = mean of |x[i] - x[i-1]|. Needs at least 2 values."""
    if len(x) == 0:
        raise ValueError("no valid values (input is empty or all missing)")
    if len(x) < 2:
        raise ValueError("need at least 2 values to compute a moving range (n = 1)")
    return sum(abs(x[i] - x[i - 1]) for i in range(1, len(x))) / (len(x) - 1)


def hourly_means(df):
    """Average each unit's 15-min readings within each clock hour.

    df needs columns unit_id, timestamp, temp_c. Blank temp_c is skipped; an hour with no
    valid reading is dropped. Only complete hours are kept: the hour must have all 4 rows
    (15-min timestamps) in df, so partial hours at the edges of a week are dropped, and at
    least 3 valid temp_c values (2 or more blanks drops the hour).
    Returns a DataFrame: unit_id, hour, temp_c (mean), n_readings (valid values).
    """
    d = df[["unit_id", "timestamp", "temp_c"]].copy()
    d["timestamp"] = pd.to_datetime(d["timestamp"])
    d["hour"] = d["timestamp"].dt.floor("h")
    g = d.groupby(["unit_id", "hour"], as_index=False).agg(
        temp_c=("temp_c", "mean"), n_readings=("temp_c", "count"),
        n_rows=("timestamp", "size"))
    g = g[(g["n_rows"] == 4) & (g["n_readings"] >= 3)]
    return g.drop(columns="n_rows").reset_index(drop=True)


def imr(values, baseline=None):
    """Individuals and moving-range (I-MR) chart.

    center = mean(baseline, or values if no baseline); MR-bar = mean |x[i] - x[i-1]| of the
    baseline; sigma = MR-bar / 1.128; UCL/LCL = center +/- 3 sigma; MR chart UCL = 3.267 MR-bar.
    Every point in `values` is judged against those limits. Flags (one per valid value, in order):
    a point beyond the limits, or the 8th and later point of a run of 8+ on one side of the
    center line (a point exactly on the center breaks the run). Assumes roughly normal,
    independent points; missing values are skipped (the moving range joins their neighbours).
    """
    x = _clean(values)
    base = x if baseline is None else _clean(baseline)
    mr_bar = _mr_bar(base)
    center = sum(base) / len(base)
    sigma = mr_bar / D2
    ucl, lcl = center + 3 * sigma, center - 3 * sigma

    beyond = [v > ucl or v < lcl for v in x]
    run_flags, run, side = [], 0, 0
    for v in x:
        s = 1 if v > center else (-1 if v < center else 0)
        run = run + 1 if (s != 0 and s == side) else (1 if s != 0 else 0)
        side = s
        run_flags.append(run >= 8)
    flags = [a or b for a, b in zip(beyond, run_flags)]
    result = {
        "n": len(x), "center": center, "mr_bar": mr_bar, "sigma": sigma,
        "ucl": ucl, "lcl": lcl, "mr_ucl": D4 * mr_bar,
        "beyond_limits": beyond, "run_flags": run_flags, "flags": flags,
        "any_signal": any(flags),
    }
    if sigma == 0:
        result["message"] = "no variation in the baseline: limits collapse to the center line"
    return result


def capability(values, lsl=LSL, usl=USL):
    """Cp/Cpk (within sigma) and Pp/Ppk (overall sigma) against the spec limits.

    Within: sigma_w = MR-bar / 1.128; Cp = (usl - lsl) / (6 sigma_w);
            Cpk = min(usl - mean, mean - lsl) / (3 sigma_w).
    Overall: s = sample standard deviation (n - 1); Pp = (usl - lsl) / (6 s);
            Ppk = min(usl - mean, mean - lsl) / (3 s).
    capable = Ppk >= 1.33. The flag is on Ppk because consecutive 15-min readings are
    autocorrelated, so MR-bar understates the spread and Cpk is optimistic.
    With zero variation the indices are None (undefined) and `message` says so.
    """
    x = _clean(values)
    mr_bar = _mr_bar(x)
    n = len(x)
    mean = sum(x) / n
    sigma = mr_bar / D2
    s = math.sqrt(sum((v - mean) ** 2 for v in x) / (n - 1))
    out = {"n": n, "mean": mean, "mr_bar": mr_bar, "sigma": sigma, "overall_sd": s,
           "lsl": lsl, "usl": usl, "cp": None, "cpk": None, "pp": None, "ppk": None,
           "capable": None, "message": ""}
    if sigma == 0 or s == 0:
        out["message"] = "no variation: Cp, Cpk, Pp and Ppk are undefined"
        return out
    k = min(usl - mean, mean - lsl)
    out["cp"] = (usl - lsl) / (6 * sigma)
    out["cpk"] = k / (3 * sigma)
    out["pp"] = (usl - lsl) / (6 * s)
    out["ppk"] = k / (3 * s)
    out["capable"] = out["ppk"] >= CPK_MIN
    return out


def stuck_probe(values, run_len=12, last_n=96):
    """Flag a probably stuck probe on raw readings.

    stuck = True if there are >= run_len identical consecutive readings anywhere (12 = 3 h),
    or MR-bar = 0 over the last last_n readings (96 = 24 h). Missing readings are skipped.
    Returns stuck, longest_run, flat_last_window, message.
    """
    x = _clean(values)
    longest = run = 0
    for i, v in enumerate(x):
        run = run + 1 if i > 0 and v == x[i - 1] else 1
        longest = max(longest, run)
    tail = x[-last_n:]
    flat = len(tail) >= 2 and _mr_bar(tail) == 0
    stuck = longest >= run_len or flat
    msg = "check probe: readings are not changing" if stuck else ""
    return {"stuck": stuck, "longest_run": longest, "flat_last_window": flat, "message": msg}


def fridge_summary(df, unit_id):
    """Apply the SPEC method to one unit of fridge_telemetry.csv.

    Baseline week = first 7 days, latest week = last 7 days (by timestamp). Returns:
    imr (hourly means, baseline limits, latest-week points), capability (raw latest-week
    readings; flag on Ppk), signals_last_24h / imr_alert (I-MR signals among the last 24 hourly
    means), stuck_probe (raw latest-week readings), out_of_spec (raw latest-week readings
    outside 1-6 C).
    """
    d = df[df["unit_id"] == unit_id].copy()
    if d.empty:
        raise ValueError(f"no rows for unit {unit_id}")
    d["timestamp"] = pd.to_datetime(d["timestamp"])
    d = d.sort_values("timestamp")
    start, end = d["timestamp"].min(), d["timestamp"].max()
    week = pd.Timedelta(days=7)
    base = d[d["timestamp"] < start + week]
    latest = d[d["timestamp"] > end - week]

    base_h = hourly_means(base)["temp_c"].tolist()
    latest_h = hourly_means(latest)
    chart = imr(latest_h["temp_c"].tolist(), baseline=base_h)
    flags = chart["flags"]
    signals_24h = sum(flags[-24:])
    chart["n_baseline_hours"] = len(base_h)
    chart["hours"] = [t.strftime("%Y-%m-%dT%H:%M:%S") for t in latest_h["hour"]]

    raw = latest["temp_c"]
    bad = latest[(raw > USL) | (raw < LSL)]
    out_of_spec = [{"timestamp": t.strftime("%Y-%m-%dT%H:%M:%S"), "temp_c": float(v)}
                   for t, v in zip(bad["timestamp"], bad["temp_c"])]
    return {
        "unit_id": unit_id,
        "imr": chart,
        "signals_last_24h": signals_24h,
        "imr_alert": signals_24h > 0,
        "capability": capability(raw.tolist()),
        "stuck_probe": stuck_probe(raw.tolist()),
        "out_of_spec": out_of_spec,
    }


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------
U_LIMIT = 0.01        # flag a generator when average unavailability U exceeds 1 %


def p_chart(failures, tests, baseline=None):
    """p-chart of the failure rate per subgroup (one subgroup per list position).

    p_i = failures_i / tests_i; p-bar = sum(failures) / sum(tests) over the baseline;
    sigma_i = sqrt(p-bar (1 - p-bar) / n_i); UCL_i = p-bar + 3 sigma_i;
    LCL_i = max(0, p-bar - 3 sigma_i). A subgroup is flagged if p_i > UCL_i, or p_i < LCL_i
    when LCL_i > 0. np_bar = p-bar * mean(n of the baseline); small_n_warning when np_bar < 5
    (the normal approximation behind the limits is then rough).
    baseline = (failures, tests) lists used for p-bar (Phase I); every subgroup is then judged
    against those limits (Phase II). Without a baseline, p-bar is pooled from all subgroups.
    If p-bar = 0 the limits are None; with a baseline the message is "no failures in the
    baseline: any failure is a signal" and any subgroup with failures > 0 is flagged; without
    one the message is "no failures observed: p-chart limits not defined" and nothing is flagged.
    Assumes independent tests with the same failure probability. A subgroup with 0 tests, empty
    input or mismatched lists is a ValueError.
    """
    f, n = list(failures), list(tests)
    bf, bn = (f, n) if baseline is None else (list(baseline[0]), list(baseline[1]))
    for ff, nn in ((f, n), (bf, bn)):
        if len(ff) == 0 or len(ff) != len(nn):
            raise ValueError("failures and tests must be non-empty lists of the same length")
        if any(t <= 0 for t in nn):
            raise ValueError("every subgroup needs at least 1 test (found 0 or fewer)")
        if any(a < 0 or a > t for a, t in zip(ff, nn)):
            raise ValueError("failures must be between 0 and the number of tests")
    p = [a / t for a, t in zip(f, n)]
    p_bar = sum(bf) / sum(bn)
    np_bar = p_bar * sum(bn) / len(bn)
    out = {"p": p, "p_bar": p_bar, "np_bar": np_bar, "small_n_warning": np_bar < 5,
           "sigma": None, "ucl": None, "lcl": None, "flags": [False] * len(p),
           "any_signal": False, "message": ""}
    if p_bar == 0:
        if baseline is None:
            out["message"] = "no failures observed: p-chart limits not defined"
        else:
            out["message"] = "no failures in the baseline: any failure is a signal"
            out["flags"] = [a > 0 for a in f]
            out["any_signal"] = any(out["flags"])
        return out
    sigma = [math.sqrt(p_bar * (1 - p_bar) / t) for t in n]
    ucl = [p_bar + 3 * sg for sg in sigma]
    lcl = [max(0.0, p_bar - 3 * sg) for sg in sigma]
    flags = [pi > u or (l > 0 and pi < l) for pi, u, l in zip(p, ucl, lcl)]
    out.update(sigma=sigma, ucl=ucl, lcl=lcl, flags=flags, any_signal=any(flags))
    return out


def poisson_upper(n, alpha=0.025):
    """Exact upper confidence limit mu_U for a Poisson mean given n observed events.

    Solves P(X <= n; mu_U) = alpha (cumulative Poisson) by bisection; alpha = 0.025 gives the
    upper end of a two-sided 95 % interval (n = 0 -> 3.6889, 1 -> 5.5716, 2 -> 7.2247).
    """
    if n < 0 or int(n) != n:
        raise ValueError("n must be a non-negative whole number")
    if not 0 < alpha < 1:
        raise ValueError("alpha must be between 0 and 1")

    def cdf(mu):
        term = total = math.exp(-mu)
        for k in range(1, int(n) + 1):
            term *= mu / k
            total += term
        return total

    lo, hi = 0.0, float(n) + 10.0
    while cdf(hi) > alpha:           # cdf falls as mu grows; find an upper bracket
        hi *= 2
    for _ in range(200):
        mid = (lo + hi) / 2
        if cdf(mid) > alpha:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _unavailability(rate, T):
    """Exact average unavailability U = 1 - (1 - exp(-rate T)) / (rate T), via expm1."""
    x = rate * T
    return 1 + math.expm1(-x) / x


def dead_time(failures, days_in_service, T):
    """Fractional dead time of a periodically tested standby unit (constant failure rate).

    lambda = failures / days_in_service (per day); exact U = 1 - (1 - exp(-lambda T)) / (lambda T);
    approx = lambda T / 2 (good when lambda T is small); flag = U > 0.01.
    T_max_approx = 2 * 0.01 / lambda; T_max_exact solves U(T) = 0.01 by bisection.
    Assumes exponential failures found at tests. With 0 failures lambda = 0, U / approx /
    T_max are None and `message` says the estimate is not available (not zero risk).
    days_in_service <= 0 or T <= 0 is a ValueError.
    Uncertainty: n_failures is returned; when 1 <= N < 5, `caveat` says "based on only N
    failures: a rough estimate". lambda_upper = mu_U / days_in_service, where mu_U is the exact
    Poisson 95 % upper limit (poisson_upper); u_upper is the exact U at lambda_upper. The flag
    stays on the point estimate.
    """
    if days_in_service <= 0 or T <= 0:
        raise ValueError("days_in_service and T must be positive")
    if failures < 0:
        raise ValueError("failures cannot be negative")
    lam = failures / days_in_service
    out = {"lambda_per_day": lam, "T": T, "u": None, "approx": None, "flag": None,
           "t_max_approx": None, "t_max_exact": None, "n_failures": failures,
           "caveat": "", "lambda_upper": None, "u_upper": None,
           "message": "no failures observed, estimate not available"}
    if failures == 0:
        return out
    u = _unavailability(lam, T)
    lo, hi = 0.0, 1.0 / lam
    while _unavailability(lam, hi) < U_LIMIT:     # U rises with T; find an upper bracket
        hi *= 2
    for _ in range(200):
        mid = (lo + hi) / 2
        if mid == lo or mid == hi:
            break
        if _unavailability(lam, mid) < U_LIMIT:
            lo = mid
        else:
            hi = mid
    lam_up = poisson_upper(failures) / days_in_service
    caveat = f"based on only {failures} failures: a rough estimate" if failures < 5 else ""
    out.update(u=u, approx=lam * T / 2, flag=u > U_LIMIT, message="", caveat=caveat,
               lambda_upper=lam_up, u_upper=_unavailability(lam_up, T),
               t_max_approx=2 * U_LIMIT / lam, t_max_exact=(lo + hi) / 2)
    return out


def generator_summary(df, generator_id, min_tests=3):
    """Apply p_chart and dead_time to one generator in generator_tests.csv.

    Subgroups are calendar months of test_date; every test (weekly start or monthly load) is
    one start attempt, a failure is started == 0. p-chart baseline (Phase I) = all months
    BEFORE the latest month, leaving out months with fewer than min_tests (3) tests; every
    month is judged against those limits. A month with too few tests is returned with
    judged = False and the note "too few tests to judge", is never flagged and is not in p-bar.
    Needs at least 2 months and one baseline month with enough tests (else ValueError).
    Dead time uses T = 7 days and days_in_service = (last test_date - first test_date).days + 7
    (the last test covers the 7 days that follow it).
    """
    d = df[df["generator_id"] == generator_id].copy()
    if d.empty:
        raise ValueError(f"no rows for generator {generator_id}")
    d["test_date"] = pd.to_datetime(d["test_date"])
    d["month"] = d["test_date"].dt.strftime("%Y-%m")
    d["failed"] = (d["started"] == 0).astype(int)
    g = d.groupby("month").agg(tests=("failed", "size"), failures=("failed", "sum"))
    months, tests, fails = g.index.tolist(), g["tests"].tolist(), g["failures"].tolist()
    judged = [t >= min_tests for t in tests]
    base = [i for i in range(len(months) - 1) if judged[i]]
    if not base:
        raise ValueError("need an earlier month with enough tests to build the p-chart baseline")
    chart = p_chart(fails, tests, baseline=([fails[i] for i in base], [tests[i] for i in base]))
    chart["flags"] = [fl and j for fl, j in zip(chart["flags"], judged)]
    chart["any_signal"] = any(chart["flags"])
    chart.update(months=months, tests=tests, failures=fails, judged=judged,
                 notes=["" if j else "too few tests to judge" for j in judged],
                 baseline_months=[months[i] for i in base])
    days = (d["test_date"].max() - d["test_date"].min()).days + 7
    dt = dead_time(int(d["failed"].sum()), days, 7)
    dt["days_in_service"] = days
    return {"generator_id": generator_id, "p_chart": chart, "dead_time": dt}

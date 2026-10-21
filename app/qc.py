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

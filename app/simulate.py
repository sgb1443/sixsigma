"""Invented test data for the blood bank tool: building blocks plus the random scenario.

Standard library plus pandas. All data is INVENTED. Same columns, formats and 14-day window
(ending 2026-10-20T06:00:00) as data/fridge_telemetry.csv and friends.
The scenario 2 and 3 files in data/ are made from these same building blocks by data/make_data.py.
Scenario 1 (data/make_data.py, fridge_unit) keeps its own older code so its files never change.
Everything is deterministic: the same random.Random gives the same rows.
"""
import csv
import io
import random
from datetime import datetime, timedelta, date

import pandas as pd

END = datetime(2026, 10, 20, 6, 0)   # last reading
N = 14 * 96                          # 14 days of 15-minute readings = 1344
START = END - timedelta(minutes=15 * (N - 1))
DRIFT_FROM = 960                     # same drift start as FR-02 in scenario 1 (last 4 days)

FRIDGES = ("FR-01", "FR-02", "FR-03", "FR-04")
GENERATORS = ("GEN-A", "GEN-B")
FRIDGE_FIELDS = ["unit_id", "timestamp", "temp_c", "door_open", "power_on", "compressor_duty"]
GEN_FIELDS = ["generator_id", "test_date", "test_type", "started", "seconds_to_start",
              "battery_v", "fuel_pct", "output_stable"]
LOG_FIELDS = ["equipment_id", "service_date", "service_type", "technician", "notes"]
MONTHLY = [date(2026, 5, 13), date(2026, 6, 10), date(2026, 7, 8),
           date(2026, 8, 12), date(2026, 9, 9), date(2026, 10, 14)]   # Wednesdays


def stamp(i):
    """Timestamp text of reading number i (0 = first)."""
    return (START + timedelta(minutes=15 * i)).strftime("%Y-%m-%dT%H:%M:%S")


def weekly_dates(last):
    """26 weekly test dates, one a week, the latest on `last` (a date)."""
    return [last - timedelta(days=7 * k) for k in range(25, -1, -1)]


def fridge_rows(unit_id, rng, door_rate=0.006, door_lengths=(1,), duty_mean=40, drift=0.0,
                outage=None, extra_doors=(), stuck=False, cap=None, quiet_last_day=True):
    """Row dicts for one fridge.

    door_rate / door_lengths: how often the door opens, and for how many readings.
    drift: degrees C the mean creeps up from reading DRIFT_FROM to the end (duty rises 25 points).
    outage: (first reading index, number of readings, peak temperature in C): power off, the
            temperature climbs to the peak at the last power-off reading, then recovers.
    extra_doors: (first reading index, length) door openings added on top of the random ones.
    stuck: the last 24 h are exactly 4.0 C.   cap: the highest temperature allowed.
    quiet_last_day: no random door openings in the last 24 h. The I-MR on hourly means flags the
            hour of every door opening (it heats that hour by about 0.3 C), so a fridge that must
            show nothing else needs a quiet last day.
    """
    door = [0] * N
    i = 0
    while i < N:
        busy = 1.0 if 7 <= (START + timedelta(minutes=15 * i)).hour <= 18 else 0.2
        if rng.random() < door_rate * busy * 2:
            length = rng.choice(door_lengths)
            for k in range(length):
                if i + k < N:
                    door[i + k] = 1
            i += length
        i += 1
    if quiet_last_day:
        door[-96:] = [0] * 96
    for first, length in extra_doors:
        for k in range(length):
            door[first + k] = 1

    rows, noise, bump, excess = [], 0.0, 0.0, 0.0
    for i in range(N):
        mean, duty = 4.0, rng.gauss(duty_mean, 2.5)
        if drift and i >= DRIFT_FROM:
            frac = (i - DRIFT_FROM) / (N - 1 - DRIFT_FROM)
            mean += drift * frac
            duty += 25 * frac
        noise = 0.6 * noise + rng.gauss(0, 0.12)      # correlated noise, like a real fridge
        bump = 0.55 * bump + (0.7 if door[i] else 0.0)  # door heat, decays after closing
        power = 1
        if outage:
            first, length, peak = outage
            if first <= i < first + length:
                power, duty = 0, 0
                excess = (peak - 4.0) * (i - first + 1) / length
            else:
                excess *= 0.75                         # recovering once power is back
                if i == first + length:
                    duty = 90
        temp = mean + noise + bump + excess
        if cap:
            temp = min(temp, cap)
        rows.append({"unit_id": unit_id, "timestamp": stamp(i), "temp_c": round(temp, 1),
                     "door_open": door[i], "power_on": power,
                     "compressor_duty": int(max(0, min(100, round(duty))))})
    if stuck:
        for r in rows[-96:]:
            r["temp_c"] = 4.0
    return rows


def generator_rows(rng, weekly, monthly=MONTHLY, fails=None):
    """Row dicts for GEN-A and GEN-B. fails maps generator_id -> set of ISO dates that failed."""
    fails = fails or {}
    tests = sorted([(d, "weekly start") for d in weekly] + [(d, "monthly load") for d in monthly])
    rows = []
    for d, test_type in tests:
        for g in GENERATORS:
            fuel = rng.randint(62, 95)
            secs = round(rng.uniform(3.2, 6.8), 1)
            batt = round(rng.uniform(12.7, 13.2), 2)
            started, stable = 1, ("" if test_type == "weekly start" else 1)
            if d.isoformat() in fails.get(g, ()):
                started, secs, batt = 0, "", 11.8
                if test_type == "monthly load":
                    stable = ""
            rows.append({"generator_id": g, "test_date": d.isoformat(), "test_type": test_type,
                         "started": started, "seconds_to_start": secs, "battery_v": batt,
                         "fuel_pct": fuel, "output_stable": stable})
    return rows


def to_frame(rows, fields, **read_options):
    """Rows -> DataFrame exactly as pd.read_csv would read the CSV file made from them."""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    buf.seek(0)
    return pd.read_csv(buf, **read_options)


# ---------------------------------------------------------------------------
# Scenario 4: random data
# ---------------------------------------------------------------------------
FRIDGE_BEHAVIOURS = ["healthy", "drifting warm", "door-seal problem", "stuck probe", "power outage"]
TECHS = ["Ana", "Ben", "Ravi", "MK"]
LIMITS = {"coil cleaning": 90, "probe calibration": 180, "full service": 365,
          "generator service": 365}


def clock(i):
    """'YYYY-MM-DD HH:MM' of reading number i, for the answer key."""
    return stamp(i)[:16].replace("T", " ")


def random_fridge(unit_id, rng):
    """(rows, plain-language description) of one randomly chosen fridge behaviour."""
    kind = rng.choice(FRIDGE_BEHAVIOURS)
    if kind == "drifting warm":
        rise = round(rng.uniform(0.9, 1.3), 1)
        return (fridge_rows(unit_id, rng, drift=rise, cap=5.4),
                f"Drifting warm: the mean creeps up by about {rise} C over the last 4 days "
                f"(from {clock(DRIFT_FROM)}), staying below 5.5 C.")
    if kind == "door-seal problem":
        first, length = rng.randint(N - 90, N - 10), rng.randint(2, 4)
        return (fridge_rows(unit_id, rng, door_rate=0.022, door_lengths=(1, 2, 3, 4), duty_mean=55,
                            extra_doors=[(first, length)], cap=5.8, quiet_last_day=False),
                f"Door-seal problem: frequent door openings all week, and the door open for "
                f"{length} readings in a row at {clock(first)}.")
    if kind == "stuck probe":
        return (fridge_rows(unit_id, rng, stuck=True),
                f"Stuck probe: temperature exactly 4.0 C for the last 24 h (from {clock(N - 96)}).")
    if kind == "power outage":
        first, length = rng.randint(N - 90, N - 40), rng.randint(8, 16)
        high = rng.random() < 0.4
        peak = round(rng.uniform(6.2, 7.0) if high else rng.uniform(4.8, 5.4), 1)
        return (fridge_rows(unit_id, rng, outage=(first, length, peak)),
                f"Power outage: power off for {length} readings from {clock(first)}, temperature "
                f"climbs to about {peak} C at {clock(first + length - 1)}"
                f"{' (above 6 C: expect CRITICAL)' if high else ''}, then recovers.")
    return (fridge_rows(unit_id, rng),
            "Healthy: about 4 C, brief single door openings, steady compressor duty.")


def random_log(rng, overdue):
    """Maintenance log rows (all within interval) and the answer for the one overdue item, if any.

    overdue = (equipment_id, service_type) or None.
    """
    rows, note = [], ""
    for unit in FRIDGES + GENERATORS:
        types = ["generator service"] if unit.startswith("GEN") else \
            ["coil cleaning", "probe calibration", "full service"]
        for t in types:
            limit = LIMITS[t]
            age = rng.randint(15, int(limit * 0.8))
            if overdue == (unit, t):
                age = limit + rng.randint(10, 60)
                note = f"{t} overdue ({age} days since the last one, interval {limit})"
            when = (END.date() - timedelta(days=age)).isoformat()
            rows.append({"equipment_id": unit, "service_date": when, "service_type": t,
                         "technician": rng.choice(TECHS), "notes": "Routine service (invented)"})
    return rows, note


def random_scenario(seed):
    """Return (fridge_df, generator_df, log_df, answer_key) for this seed. Deterministic."""
    rng = random.Random(seed)
    fridge, key = [], {}
    for u in FRIDGES:
        rows, key[u] = random_fridge(u, rng)
        fridge += rows

    weekly = weekly_dates(date(2026, 10, 20))
    fails = {}
    for g in GENERATORS:
        if rng.random() < 0.4:
            fails[g] = {weekly[-1].isoformat()}
            if rng.random() < 0.5:
                fails[g].add(weekly[-3].isoformat())
            key[g] = (f"Failing: failed to start at the weekly test(s) on {', '.join(sorted(fails[g]))}"
                      " (battery 11.8 V).")
        else:
            key[g] = "Healthy: starts in 3-7 s, battery and fuel fine."
    gens = generator_rows(rng, weekly, fails=fails)

    overdue = None
    if rng.random() < 0.35:
        overdue = rng.choice([(u, t) for u in FRIDGES for t in LIMITS if t != "generator service"]
                             + [(g, "generator service") for g in GENERATORS])
    log, note = random_log(rng, overdue)
    if overdue:
        key[overdue[0]] += " Also " + note + "."

    return (to_frame(fridge, FRIDGE_FIELDS), to_frame(gens, GEN_FIELDS),
            to_frame(log, LOG_FIELDS, dtype=str, keep_default_na=False), key)

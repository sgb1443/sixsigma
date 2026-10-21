"""Make the invented test data for the blood bank equipment tool.

Run from the repo root:   python data/make_data.py
Uses only the Python standard library. Fixed seed, so the files are identical every run.
All data is INVENTED. See data/README.md for the story planted in each unit.
"""
import csv
import random
from datetime import datetime, timedelta, date
from pathlib import Path

OUT = Path(__file__).resolve().parent
SEED = 20261020
END = datetime(2026, 10, 20, 6, 0)   # last reading
N = 14 * 96                          # 14 days of 15-minute readings = 1344
START = END - timedelta(minutes=15 * (N - 1))


def fridge_unit(unit_id, rng):
    """Return a list of row dicts for one fridge."""
    rows = []
    noise = 0.0   # AR(1) noise, so neighbouring readings are correlated like a real fridge
    bump = 0.0    # heat from door openings, decays after the door closes
    # door openings: list of start indexes and lengths (in readings)
    door = [0] * N
    if unit_id == "FR-01":
        rate, lengths = 0.006, [1]            # brief, rare
    elif unit_id == "FR-03":
        rate, lengths = 0.022, [1, 2, 3, 4]   # frequent, some long
    else:
        rate, lengths = 0.006, [1]
    i = 0
    while i < N:
        hour = (START + timedelta(minutes=15 * i)).hour
        busy = 1.0 if 7 <= hour <= 18 else 0.2   # doors open mostly in the daytime
        if rng.random() < rate * busy * 2:
            L = rng.choice(lengths)
            for k in range(L):
                if i + k < N:
                    door[i + k] = 1
            i += L
        i += 1

    power = [1] * N
    if unit_id == "FR-02":
        p0 = 4 * 96 + 8   # 2026-10-10 08:15 + ... computed below in README from the timestamps
        for k in range(4):
            power[p0 + k] = 0

    for i in range(N):
        t = START + timedelta(minutes=15 * i)
        mean, duty = 4.0, rng.gauss(40, 2.5)
        sd = 0.12
        if unit_id == "FR-02":
            if i >= 960:   # after day 10 the unit creeps warm
                frac = (i - 960) / (N - 1 - 960)
                mean += 1.2 * frac
                duty += 25 * frac
        if unit_id == "FR-03":
            duty = rng.gauss(55, 3)
        noise = 0.6 * noise + rng.gauss(0, sd)
        bump = 0.55 * bump + (0.7 if door[i] else 0.0)
        temp = mean + noise + bump
        if power[i] == 0:
            duty = 0
            temp += 0.2 * (sum(1 for x in power[: i + 1][-4:] if x == 0))
        elif i > 0 and power[i - 1] == 0:
            temp += 0.5       # still recovering right after power returns
            duty = 90
        if unit_id == "FR-03":
            temp = min(temp, 5.8)   # random spikes stay below 6.0; the planted one is added below
        if unit_id == "FR-02":
            temp = min(temp, 5.4)
        rows.append({
            "unit_id": unit_id,
            "timestamp": t.strftime("%Y-%m-%dT%H:%M:%S"),
            "temp_c": round(temp, 1),
            "door_open": door[i],
            "power_on": power[i],
            "compressor_duty": int(max(0, min(100, round(duty)))),
        })

    if unit_id == "FR-03":
        # planted CRITICAL event: door open 2026-10-16 14:00-14:45, peak 6.4 C at 14:45
        plan = [("14:00", 5.0, 1), ("14:15", 5.7, 1), ("14:30", 6.1, 1), ("14:45", 6.4, 1),
                ("15:00", 6.0, 0), ("15:15", 5.3, 0), ("15:30", 4.7, 0)]
        for r in rows:
            if r["timestamp"][:10] == "2026-10-16":
                for hm, tc, d in plan:
                    if r["timestamp"][11:16] == hm:
                        r["temp_c"], r["door_open"] = tc, d
    if unit_id == "FR-04":
        for r in rows[-96:]:            # stuck probe: final 24 h identical
            r["temp_c"] = 4.0
        for idx in (200, 610, 1000):    # missing readings
            rows[idx]["temp_c"] = ""
    return rows


def write_fridge(rng):
    with open(OUT / "fridge_telemetry.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["unit_id", "timestamp", "temp_c", "door_open",
                                          "power_on", "compressor_duty"])
        w.writeheader()
        for u in ("FR-01", "FR-02", "FR-03", "FR-04"):
            w.writerows(fridge_unit(u, rng))


def write_generators(rng):
    weekly = [date(2026, 10, 20) - timedelta(days=7 * k) for k in range(25, -1, -1)]  # 26 Tuesdays
    monthly = [date(2026, 5, 13), date(2026, 6, 10), date(2026, 7, 8),
               date(2026, 8, 12), date(2026, 9, 9), date(2026, 10, 14)]  # Wednesdays
    # GEN-B planted trouble (date -> values). seconds None = did not start.
    b_slow = {"2026-09-15": 10.8, "2026-09-22": 12.5, "2026-09-29": 14.1, "2026-10-13": 16.8,
              "2026-10-14": 13.5}
    b_fail = {"2026-10-06", "2026-10-20"}
    rows = []
    tests = [(d, "weekly start") for d in weekly] + [(d, "monthly load") for d in monthly]
    tests.sort()
    for d, ttype in tests:
        ds = d.isoformat()
        for g in ("GEN-A", "GEN-B"):
            fuel = rng.randint(62, 95) if g == "GEN-A" else rng.randint(55, 90)
            stable = "" if ttype == "weekly start" else 1
            if g == "GEN-A":
                started, secs, batt = 1, round(rng.uniform(3.2, 6.8), 1), round(rng.uniform(12.7, 13.2), 2)
            else:
                started, secs, batt = 1, round(rng.uniform(4.0, 7.0), 1), round(rng.uniform(12.6, 13.1), 2)
                if ds >= "2026-08-25":   # battery slowly falls
                    weeks = (d - date(2026, 8, 25)).days / 7
                    batt = round(12.9 - 0.18 * weeks + rng.uniform(-0.04, 0.04), 2)
                if ds in b_slow:
                    secs = b_slow[ds]
                if ds in b_fail:
                    started, secs, batt = 0, "", 11.8
                    if ttype == "monthly load":
                        stable = ""
                if ds == "2026-10-14":
                    stable = 0
            rows.append({"generator_id": g, "test_date": ds, "test_type": ttype, "started": started,
                         "seconds_to_start": secs, "battery_v": batt, "fuel_pct": fuel,
                         "output_stable": stable})
    with open(OUT / "generator_tests.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["generator_id", "test_date", "test_type", "started",
                                          "seconds_to_start", "battery_v", "fuel_pct", "output_stable"])
        w.writeheader()
        w.writerows(rows)


def write_maintenance():
    L = [
        # equipment, date, type, tech, notes
        ("FR-01", "2025-11-18", "full service", "MK", "Annual service, door gasket checked"),
        ("FR-01", "2026-02-10", "coil cleaning", "Ana", "Condenser coils vacuumed"),
        ("FR-01", "2026-05-20", "probe calibration", "Ana", "Probe within 0.1 C of reference"),
        ("FR-01", "2026-08-12", "coil cleaning", "Ana", "Condenser coils vacuumed"),
        ("FR-02", "2025-12-04", "full service", "MK", "Annual service, compressor oil ok"),
        ("FR-02", "2026-04-09", "coil cleaning", "Ben", "Coils dusty"),
        ("FR-02", "2026-06-15", "probe calibration", "Ben", "Probe within tolerance"),
        ("FR-02", "2026-07-10", "coil cleaning", "Ben", "Last cleaning: now OVERDUE (over 90 days)"),
        ("FR-03", "2026-01-22", "full service", "Ravi", "Annual service"),
        ("FR-03", "2026-04-30", "probe calibration", "Ravi", "Probe within tolerance"),
        ("FR-03", "2026-06-05", "coil cleaning", "Ravi", "Coils clean enough"),
        ("FR-03", "2026-09-01", "coil cleaning", "Ravi", "Noted door gasket looks worn"),
        ("FR-04", "2026-03-03", "full service", "Ana", "Annual service"),
        ("FR-04", "2026-05-05", "probe calibration", "MK", "Probe within tolerance"),
        ("FR-04", "2026-07-28", "coil cleaning", "MK", "Condenser coils vacuumed"),
        ("GEN-A", "2026-03-15", "generator service", "Ravi", "Oil, filter and battery check"),
        ("GEN-B", "2026-02-20", "generator service", "Ravi", "Oil, filter and battery check"),
    ]
    with open(OUT / "maintenance_log.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["equipment_id", "service_date", "service_type", "technician", "notes"])
        w.writerows(L)


if __name__ == "__main__":
    rng = random.Random(SEED)
    write_fridge(rng)
    write_generators(rng)
    write_maintenance()
    print("wrote fridge_telemetry.csv, generator_tests.csv, maintenance_log.csv in", OUT)

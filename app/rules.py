"""Escalation rules for the blood bank monitor (SPEC.md section 6).

Plain functions, no web framework. They take pandas frames (telemetry, generator tests, log),
call app/qc.py for the statistics, and return plain dicts of alerts.

Alert fields: id, type (data / interval / event), tier, base_tier, since, last_seen, message,
task, acknowledged, escalated, retriggered.
`last_seen` is the time of the newest evidence behind the alert; the operator's "maintenance
complete" row resolves the alerts whose last_seen is not newer than it (see _finish). It is
documented per alert type below.
Tiers, lowest to highest: OK < WATCH < MAINTENANCE < URGENT < CRITICAL. Status = highest tier
among the alerts that are still active.
"""
import pandas as pd

try:                       # works from the repo root (tests) ...
    from app.qc import fridge_summary, generator_summary
except ImportError:        # ... and when Posit Connect runs app/ as the bundle root
    from qc import fridge_summary, generator_summary

TIERS = ["OK", "WATCH", "MAINTENANCE", "URGENT", "CRITICAL"]
TIERS_SORT = {"TASKS COMPLETE": 0}   # sorts together with OK (after WATCH)
COMPLETE = "maintenance complete"          # OLD unit-level button row (still read)
TASK_DONE = "task complete"                # per-alert button row; notes = the alert id
FRIDGE_INTERVALS = [          # (service_type, days, recommended task)
    ("coil cleaning", 90, "Clean the condenser coils and log it."),
    ("probe calibration", 180, "Calibrate the temperature probe against a reference."),
    ("full service", 365, "Book the annual full service."),
]
GEN_INTERVALS = [
    ("weekly start", 7, "Run the weekly start test and log it."),
    ("monthly load", 30, "Run the monthly load test and log it."),
]
GEN_SERVICE = ("generator service", 365, "Book the annual generator service.")
LIMITS = {t: d for t, d, _ in FRIDGE_INTERVALS + GEN_INTERVALS + [GEN_SERVICE]}   # service -> days
LOG_COLUMNS = ["equipment_id", "service_date", "service_type", "technician", "notes"]
DUTY_RISE = 10.0              # percentage points above the baseline week
NEAR_HIGH, NEAR_LOW = 5.5, 1.5
SPEC_LOW, SPEC_HIGH = 1.0, 6.0
DAY = pd.Timedelta(hours=24)


# ---------------------------------------------------------------------------
# Log
# ---------------------------------------------------------------------------
def make_log(bundled, entries=None):
    """Bundled maintenance_log rows PLUS client-sent entries (list of dicts), as one DataFrame.

    Adds a `when` column (a Timestamp). A date without a time counts as the END of that day,
    so an acknowledgement dated today is not older than an alert that started this morning.
    Raises ValueError if an entry lacks equipment_id, service_date or service_type.
    """
    rows = bundled.to_dict("records") if bundled is not None else []
    for e in entries or []:
        for key in ("equipment_id", "service_date", "service_type"):
            if not str(e.get(key, "")).strip():
                raise ValueError(f"log entry is missing {key}: {e}")
        rows.append(e)
    log = pd.DataFrame(rows, columns=LOG_COLUMNS).fillna("")
    for col in LOG_COLUMNS:
        log[col] = log[col].astype(str).str.strip()
    stamps = []
    for text in log["service_date"]:
        t = pd.Timestamp(text)
        stamps.append(t + pd.Timedelta(days=1) - pd.Timedelta(seconds=1) if len(text) <= 10 else t)
    log["when"] = pd.Series(stamps, index=log.index, dtype="datetime64[ns]")
    # A "task complete" row about a service also counts as that service done on that date.
    extra = []
    for _, row in log[log["service_type"] == TASK_DONE].iterrows():
        service = _task_service(row)
        if service:
            before = log[(log["equipment_id"] == row["equipment_id"]) & (log["service_type"] == service)
                         & (log["when"] < row["when"])]
            limit = LIMITS[service]
            overdue = before.empty or (row["when"].normalize() - before["when"].max().normalize()).days > limit
            # notes = the id of the overdue alert this clears ("" if the service was not overdue)
            extra.append({**row.to_dict(), "service_type": service,
                          "notes": f"{row['equipment_id']}:{service} overdue" if overdue else ""})
    return pd.concat([log, pd.DataFrame(extra, columns=log.columns)], ignore_index=True) if extra else log


def _task_service(row):
    """The service type a "task complete" row stands for, or None.

    Two note formats are written by the page: a checklist tick, "FR-03: coil cleaning", and the
    button on an overdue alert, "FR-02:coil cleaning overdue: Clean the condenser coils ...".
    """
    unit, notes = str(row["equipment_id"]).strip(), str(row["notes"]).strip()
    if notes.startswith(unit + ": "):
        name = notes[len(unit) + 2:]
    elif notes.startswith(unit + ":"):
        name = notes[len(unit) + 1:].split(": ")[0]
        name = name[:-len(" overdue")] if name.endswith(" overdue") else ""
    else:
        return None
    return name if name in LIMITS else None


def _last_service(log, equipment_id, service_type):
    """Date (Timestamp at midnight) of the last log row of this type, or None."""
    rows = log[(log["equipment_id"] == equipment_id) & (log["service_type"] == service_type)]
    return None if rows.empty else rows["when"].max().normalize()


def _acknowledged(log, equipment_id, alert_id, since):
    """True if an 'acknowledge' row for this alert id was logged at or after `since`."""
    rows = log[(log["equipment_id"] == equipment_id) & (log["service_type"] == "acknowledge")
               & (log["notes"] == alert_id)]
    return bool((rows["when"] >= since).any())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _alert(equipment_id, name, kind, tier, since, message, task, last_seen=None):
    """last_seen = time of the newest evidence; None means "as_of" (filled in by _finish)."""
    return {"id": f"{equipment_id}:{name}", "type": kind, "tier": tier, "base_tier": tier,
            "since": since, "last_seen": last_seen, "message": message, "task": task}


def _fmt(ts):
    """Timestamp -> 'YYYY-MM-DD' when it is exactly midnight, else 'YYYY-MM-DDTHH:MM:SS'."""
    ts = pd.Timestamp(ts)
    return ts.strftime("%Y-%m-%d") if ts == ts.normalize() else ts.strftime("%Y-%m-%dT%H:%M:%S")


def _finish(equipment_id, alerts, log, now):
    """Acknowledge, escalate, apply "maintenance complete", format; return the unit fields.

    Escalation: only MAINTENANCE alerts are escalated by the 24 h timer: WATCH is "not an
    action", and URGENT / CRITICAL are already at the top or beyond.
    Task complete: a "task complete" row of this unit with notes = "<alert id>: <task text>" resolves that alert
    if its time is not older than the alert's last_seen; with newer evidence the alert stays,
    marked retriggered. A task complete on an overdue-service alert also counts as that service
    (make_log adds the service row), which clears the alert and resets days_since. The old
    unit-level "maintenance complete" row resolves every alert with last_seen up to its time.
    Something resolved and nothing left active: status TASKS COMPLETE.
    """
    for a in alerts:
        if a["last_seen"] is None:
            a["last_seen"] = now
        a["acknowledged"] = _acknowledged(log, equipment_id, a["id"], a["since"])
        a["escalated"] = (a["base_tier"] == "MAINTENANCE" and not a["acknowledged"]
                          and now - a["since"] > DAY)
        if a["escalated"]:
            a["tier"] = "URGENT"
            a["message"] += " Active for more than 24 h without its task completed."
        a["retriggered"] = False

    mine = log[log["equipment_id"] == equipment_id]
    old = mine[mine["service_type"] == COMPLETE]       # old unit-level rows (kept for old logs)
    old_at = old["when"].max() if not old.empty else None
    old_who = old.sort_values("when").iloc[-1]["technician"] if not old.empty else ""
    done = mine[mine["service_type"] == TASK_DONE]
    resolved, stamps = [], []                          # stamps: (time, who) of each resolving row
    for a in list(alerts):
        rows = done[done["notes"].map(lambda n: n == a["id"] or n.startswith(a["id"] + ":"))]
        if old_at is not None and a["last_seen"] <= old_at:
            rows, at, who = old, old_at, old_who
        elif not rows.empty and a["last_seen"] <= rows["when"].max():
            at, who = rows["when"].max(), rows.sort_values("when").iloc[-1]["technician"]
        else:
            a["retriggered"] = (old_at is not None) or not rows.empty
            continue
        alerts.remove(a)
        resolved.append({"id": a["id"], "tier": a["tier"], "message": a["message"]})
        stamps.append((at, who))
    # an overdue-service alert that a service row from the page (notes = the alert id) has cleared
    active = {a["id"] for a in alerts}
    for _, row in mine[mine["notes"].str.endswith(" overdue")].iterrows():
        if row["notes"] not in active and row["service_type"] not in (TASK_DONE, COMPLETE, "acknowledge"):
            resolved.append({"id": row["notes"], "tier": "MAINTENANCE",
                             "message": f"{row['service_type'].capitalize()} logged."})
            stamps.append((row["when"], row["technician"]))
    done_at, who = (max(stamps, key=lambda x: x[0]) if stamps else (old_at, old_who))
    for a in alerts:
        a["since"], a["last_seen"] = _fmt(a["since"]), _fmt(a["last_seen"])
    status = max((a["tier"] for a in alerts), key=TIERS.index, default="OK")
    if status == "OK" and (resolved or old_at is not None):
        status = "TASKS COMPLETE"
    alerts.sort(key=lambda a: (-TIERS.index(a["tier"]), a["id"]))
    return {"status": status, "maintenance_complete_at": None if done_at is None else _fmt(done_at),
            "resolved_by": who or None, "resolved_alerts": resolved}


def _interval_alerts(equipment_id, items, log, now, first_known, extra_last=None):
    """Overdue-interval alerts and days_since. since = last service + interval days.

    With no record at all, the alert starts at `first_known` (the earliest date we have data).
    extra_last maps service_type -> a later date from another source (generator test records).
    """
    alerts, days_since = [], {}
    for service_type, days, task in items:
        last = _last_service(log, equipment_id, service_type)
        other = (extra_last or {}).get(service_type)
        if other is not None and (last is None or other > last):
            last = other
        if last is None:
            days_since[service_type] = None
            since, text = pd.Timestamp(first_known).normalize(), "no record in the log"
        else:
            days_since[service_type] = max(0, (now.normalize() - last).days)
            since, text = last + pd.Timedelta(days=days), f"last done {_fmt(last)}"
        if now.normalize() - since > pd.Timedelta(0):
            alerts.append(_alert(equipment_id, f"{service_type} overdue", "interval", "MAINTENANCE",
                                 since, f"{service_type.capitalize()} overdue ({text}, "
                                 f"interval {days} days).", task))
    return alerts, days_since


def _streak_start(hours, flags, now):
    """Start of the current signal streak on the I-MR chart.

    Walk back from the latest signal hour in the last 24 h; keep going while the previous
    signal hour is less than 24 h earlier (the alert would not have cleared in between).
    """
    sig = [h for h, f in zip(hours, flags) if f]
    recent = [h for h in sig if h >= now - DAY]
    if not recent:
        return None
    start = recent[-1]
    for h in reversed([h for h in sig if h < start]):
        if start - h < DAY:
            start = h
        else:
            break
    return start


# ---------------------------------------------------------------------------
# Fridges
# ---------------------------------------------------------------------------
def fridge_status(telemetry, unit_id, log, as_of=None):
    """Alerts and status of one fridge. Returns a dict that includes qc's fridge_summary.

    now = as_of (default: the last timestamp in `telemetry`); the last 24 h = readings after
    now - 24 h; data after `now` is ignored. `since` choices are documented per alert below.
    """
    t = telemetry.copy()
    t["timestamp"] = pd.to_datetime(t["timestamp"])
    now = pd.Timestamp(as_of) if as_of is not None else t["timestamp"].max()
    d = t[(t["unit_id"] == unit_id) & (t["timestamp"] <= now)].sort_values("timestamp")
    summary = fridge_summary(d, unit_id)
    chart, cap = summary["imr"], summary["capability"]
    last24 = d[d["timestamp"] > now - DAY]
    end = d["timestamp"].max()
    start = d["timestamp"].min()
    alerts = []

    # --- I-MR chart, last 24 hourly means. since = start of the signal streak. ---
    hours = [pd.Timestamp(h) for h in chart["hours"]]
    recent = [h >= now - DAY for h in hours]
    beyond24 = sum(b for b, r in zip(chart["beyond_limits"], recent) if r)
    run24 = any(b for b, r in zip(chart["run_flags"], recent) if r)
    since = _streak_start(hours, chart["flags"], now)
    # last_seen: end of the latest flagged hour in the last 24 h (hour start + 1 h)
    flagged = [h for h, f, r in zip(hours, chart["flags"], recent) if f and r]
    seen = flagged[-1] + pd.Timedelta(hours=1) if flagged else None
    if run24 or beyond24 >= 2:
        alerts.append(_alert(unit_id, "control chart drift", "data", "MAINTENANCE", since,
                             f"Hourly temperature outside the baseline limits {chart['lcl']:.2f}"
                             f"-{chart['ucl']:.2f} C ({beyond24} hours beyond, "
                             f"run of 8: {'yes' if run24 else 'no'}) in the last 24 h.",
                             "Inspect the unit: door seal, coils, load and set point.", seen))
    elif beyond24 == 1:
        alerts.append(_alert(unit_id, "control chart watch", "data", "WATCH", since,
                             "One hour beyond the baseline limits in the last 24 h.",
                             "Look at the chart again at the next check; no action yet.", seen))

    # --- Compressor duty. since = now (the onset is not tracked); last_seen = as_of. ---
    base_duty = d[d["timestamp"] < start + pd.Timedelta(days=7)]["compressor_duty"].mean()
    new_duty = d[d["timestamp"] > end - pd.Timedelta(days=7)]["compressor_duty"].mean()
    if pd.notna(base_duty) and pd.notna(new_duty) and new_duty - base_duty >= DUTY_RISE:
        alerts.append(_alert(unit_id, "compressor duty rising", "data", "MAINTENANCE", now,
                             f"Compressor duty averages {new_duty:.0f}% this week vs "
                             f"{base_duty:.0f}% in the baseline week (+{new_duty - base_duty:.0f} "
                             "points).", "Check the coils, door seal and refrigerant."))

    # --- Capability. Ppk < 1.33 flags; None (no variation) is not a failure here. ---
    # since = now (the onset is not tracked); last_seen = as_of.
    if cap["capable"] is False:
        alerts.append(_alert(unit_id, "capability low", "data", "MAINTENANCE", now,
                             f"Ppk {cap['ppk']:.2f} is below 1.33: the unit may not hold 1-6 C.",
                             "Review the unit's temperature performance; service if it persists."))

    # --- Stuck probe. since = start of the latest run of >= 12 identical readings. ---
    if summary["stuck_probe"]["stuck"]:
        raw = d[d["timestamp"] > end - pd.Timedelta(days=7)].dropna(subset=["temp_c"])
        run_start, run_len, since_stuck = 0, 0, raw["timestamp"].iloc[0]
        seen_stuck = since_stuck       # last_seen: the last reading of the latest run of >= 12
        temps = raw["temp_c"].tolist()
        for i, v in enumerate(temps):
            if i > 0 and v == temps[i - 1]:
                run_len += 1
            else:
                run_start, run_len = i, 1
            if run_len >= 12:
                since_stuck = raw["timestamp"].iloc[run_start]
                seen_stuck = raw["timestamp"].iloc[i]
        alerts.append(_alert(unit_id, "stuck probe", "data", "MAINTENANCE", since_stuck,
                             "Check probe: readings are not changing.",
                             "Check or replace the temperature probe; verify with a reference.",
                             seen_stuck))

    # --- Readings outside 1-6 C. ---
    bad_recent = last24[(last24["temp_c"] > SPEC_HIGH) | (last24["temp_c"] < SPEC_LOW)]
    # last_seen = the latest such reading
    if not bad_recent.empty:         # CRITICAL, since = first such reading in the last 24 h
        alerts.append(_alert(unit_id, "reading outside 1-6 C", "event", "CRITICAL",
                             bad_recent["timestamp"].iloc[0],
                             f"{len(bad_recent)} reading(s) outside 1-6 C in the last 24 h "
                             f"(min {bad_recent['temp_c'].min():.1f}, "
                             f"max {bad_recent['temp_c'].max():.1f}).",
                             "Move the blood now and quarantine it for review.",
                             bad_recent["timestamp"].iloc[-1]))
        alerts[-1]["moves_blood"] = True    # its task is a blood move: the page shows a Move blood button
    elif summary["out_of_spec"]:     # earlier in the week; since = the latest such reading
        last_bad = pd.Timestamp(summary["out_of_spec"][-1]["timestamp"])
        alerts.append(_alert(unit_id, "excursion to review", "data", "MAINTENANCE", last_bad,
                             f"{len(summary['out_of_spec'])} reading(s) outside 1-6 C earlier "
                             f"this week, last at {_fmt(last_bad)}.",
                             "Review that excursion: find the cause and decide on the blood.", last_bad))

    # --- Sudden events in the last 24 h. since = first matching reading. ---
    off = last24[last24["power_on"] == 0]
    if not off.empty:
        alerts.append(_alert(unit_id, "power loss", "event", "MAINTENANCE", off["timestamp"].iloc[0],
                             f"Power was off for {len(off)} reading(s) in the last 24 h.",
                             "Check the power supply and the temperature after the outage.",
                             off["timestamp"].iloc[-1]))   # last_seen: latest power-off reading
    door = last24["door_open"].tolist()
    pairs = [i for i in range(1, len(door)) if door[i] == 1 and door[i - 1] == 1]
    if pairs:
        alerts.append(_alert(unit_id, "door open", "event", "MAINTENANCE",
                             last24["timestamp"].iloc[pairs[0] - 1],
                             "Door open on 2 or more consecutive readings (over 5 min) "
                             "in the last 24 h.", "Check the door closes and the seal is tight.",
                             last24["timestamp"].iloc[pairs[-1]]))   # last_seen: latest such reading

    # --- Service intervals. ---
    interval, days_since = _interval_alerts(unit_id, FRIDGE_INTERVALS, log, now, start)
    alerts += interval

    # --- URGENT: a MAINTENANCE alert is active and a reading is near the limits. ---
    near = last24[(last24["temp_c"] >= NEAR_HIGH) | (last24["temp_c"] <= NEAR_LOW)]
    if not near.empty and any(a["tier"] == "MAINTENANCE" for a in alerts):
        alerts.append(_alert(unit_id, "temperature near the limit", "event", "URGENT",
                             near["timestamp"].iloc[0],
                             f"Reading at {near['temp_c'].iloc[0]:.1f} C (>= 5.5 or <= 1.5) while "
                             "a maintenance alert is active.",
                             "Swap the unit and move the blood to a backup unit.",
                             near["timestamp"].iloc[-1]))   # last_seen: latest near-limit reading
        alerts[-1]["moves_blood"] = True

    fields = _finish(unit_id, alerts, log, now)
    return {"unit_id": unit_id, "as_of": _fmt(now), **fields, "alerts": alerts,
            "days_since": days_since, "summary": summary}


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------
def generator_status(tests, gen_id, log, as_of):
    """Alerts and status of one generator. `as_of` is a Timestamp; later tests are ignored.

    Test alerts judge only the latest test of each test_type, so they clear on the next passing
    test; their `since` is that test's date. p-chart alert: since = date of the first failed
    start in the latest month. Dead-time alert: since = date of the latest failed start.
    """
    now = pd.Timestamp(as_of)
    t = tests.copy()
    t["test_date"] = pd.to_datetime(t["test_date"])
    g = t[(t["generator_id"] == gen_id) & (t["test_date"] <= now.normalize())]
    g = g.sort_values("test_date")
    summary = generator_summary(g, gen_id)
    alerts = []

    for test_type, rows in g.groupby("test_type"):
        row = rows.iloc[-1]
        when = row["test_date"]
        checks = [
            ("failed start", row["started"] == 0, "The generator did not start."),
            ("slow start", row["seconds_to_start"] > 10,
             f"Start took {row['seconds_to_start']} s (limit 10 s)."),
            ("low battery", row["battery_v"] < 12.2, f"Battery {row['battery_v']} V (limit 12.2 V)."),
            ("low fuel", row["fuel_pct"] < 50, f"Fuel {row['fuel_pct']}% (limit 50%)."),
            ("unstable output", row["output_stable"] == 0, "Output was not stable."),
        ]
        for name, bad, text in checks:
            if bad:
                # last_seen = that test's date at 00:00 (the date itself)
                alerts.append(_alert(gen_id, f"{name} ({test_type})", "event", "MAINTENANCE", when,
                                     f"{text} Latest {test_type} test, {_fmt(when)}.",
                                     f"Open a maintenance ticket; a passing {test_type} test "
                                     "clears this.", when))

    failed = g[g["started"] == 0]
    chart = summary["p_chart"]
    if chart["flags"][-1]:
        month = chart["months"][-1]
        in_month = failed[failed["test_date"].dt.strftime("%Y-%m") == month]
        since = in_month["test_date"].min() if not in_month.empty else pd.Timestamp(month + "-01")
        alerts.append(_alert(gen_id, "p-chart signal", "data", "MAINTENANCE", since,
                             f"Start-failure rate in {month} is above the p-chart limit.",
                             "Investigate the cause of the failed starts.",
                             failed["test_date"].max()))   # last_seen: latest failed start

    dt = summary["dead_time"]
    if dt["flag"]:
        caveat = f", {dt['caveat']}" if dt["caveat"] else ""
        alerts.append(_alert(gen_id, "dead-time risk", "data", "MAINTENANCE",
                             failed["test_date"].max(),
                             f"Dead-time risk {dt['u'] * 100:.1f}% (95% upper "
                             f"{dt['u_upper'] * 100:.1f}%){caveat}. Fix the cause first; until "
                             "then test more often than weekly.",
                             "Fix the cause of the failures; test more often than weekly.",
                             failed["test_date"].max()))   # last_seen: latest failed start

    last_test = {tt: rows["test_date"].max().normalize() for tt, rows in g.groupby("test_type")}
    first_known = g["test_date"].min()
    interval, days_since = _interval_alerts(gen_id, GEN_INTERVALS + [GEN_SERVICE], log, now,
                                            first_known, extra_last=last_test)
    alerts += interval

    fields = _finish(gen_id, alerts, log, now)
    return {"generator_id": gen_id, "as_of": _fmt(now), **fields, "alerts": alerts,
            "days_since": days_since, "summary": summary}


# ---------------------------------------------------------------------------
# Attention list
# ---------------------------------------------------------------------------
def _rank(status):
    """Sort rank of a status; TASKS COMPLETE ranks with OK."""
    return TIERS.index(status) if status in TIERS else TIERS_SORT[status]


def _row(unit_id, kind, r):
    """One unit of the attention list: the fields of fridge_status / generator_status."""
    keys = ("status", "alerts", "maintenance_complete_at", "resolved_by", "resolved_alerts",
            "days_since")
    return {"id": unit_id, "kind": kind, **{k: r[k] for k in keys}}


def attention_list(telemetry, tests, log, as_of=None):
    """All units, sorted CRITICAL > URGENT > MAINTENANCE > WATCH > OK, then by id."""
    now = pd.Timestamp(as_of) if as_of is not None else pd.to_datetime(telemetry["timestamp"]).max()
    rows = []
    for unit in sorted(telemetry["unit_id"].unique()):
        r = fridge_status(telemetry, unit, log, now)
        rows.append(_row(unit, "fridge", r))
    for gen in sorted(tests["generator_id"].unique()):
        r = generator_status(tests, gen, log, now)
        rows.append(_row(gen, "generator", r))
    for row in rows:
        row["tasks"] = [a["task"] for a in row["alerts"]]
    rows.sort(key=lambda r: (-_rank(r["status"]), r["id"]))
    return {"as_of": _fmt(now), "units": rows}

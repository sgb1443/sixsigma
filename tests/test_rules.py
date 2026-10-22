"""Tests for app/rules.py: each escalation tier on small hand-built data (SPEC.md section 6).

The fake fridge FR-T has 14 days of 15-min readings ending 2026-10-20 06:00. Hourly means cycle
3.9 / 4.0 / 4.1, so the baseline limits are about 4.0 +/- 0.35 and nothing signals by default.
"""
import pandas as pd

from app import rules

NOW = pd.Timestamp("2026-10-20 06:00")


def make_fridge(changes=None, duty=40):
    """Telemetry for unit FR-T. changes = {Timestamp: temp_c} overrides single raw readings."""
    stamps = pd.date_range("2026-10-06 06:15", "2026-10-20 06:00", freq="15min")
    temps = [4.0 + 0.1 * ((t.hour % 3) - 1) for t in stamps]
    df = pd.DataFrame({"unit_id": "FR-T", "timestamp": stamps, "temp_c": temps,
                       "door_open": 0, "power_on": 1, "compressor_duty": duty})
    for stamp, value in (changes or {}).items():
        df.loc[df["timestamp"] == pd.Timestamp(stamp), "temp_c"] = value
    return df


def hour_at(df, start, value):
    """Set all four readings of the hour starting at `start` to `value`."""
    df.loc[(df["timestamp"] >= start) & (df["timestamp"] < pd.Timestamp(start) + pd.Timedelta(hours=1)),
           "temp_c"] = value


def good_log(**overrides):
    """A log where every service was done recently (dates can be overridden by type)."""
    dates = {"coil cleaning": "2026-10-01", "probe calibration": "2026-10-01",
             "full service": "2026-10-01"}
    dates.update(overrides)
    rows = [{"equipment_id": "FR-T", "service_date": d, "service_type": s,
             "technician": "x", "notes": ""} for s, d in dates.items()]
    return rules.make_log(pd.DataFrame(rows, columns=rules.LOG_COLUMNS))


def ids(result):
    return [a["id"] for a in result["alerts"]]


def test_ok_when_nothing_is_wrong():
    r = rules.fridge_status(make_fridge(), "FR-T", good_log())
    assert r["status"] == "OK" and r["alerts"] == []


def test_watch_with_one_beyond_limit_hour():
    df = make_fridge()
    hour_at(df, "2026-10-20 02:00", 5.0)        # one hour at 5.0: beyond the limits, still in spec
    r = rules.fridge_status(df, "FR-T", good_log())
    assert r["status"] == "WATCH"
    assert ids(r) == ["FR-T:control chart watch"]


def test_maintenance_with_two_beyond_limit_hours():
    df = make_fridge()
    hour_at(df, "2026-10-20 02:00", 5.0)
    hour_at(df, "2026-10-20 04:00", 5.0)
    r = rules.fridge_status(df, "FR-T", good_log())
    assert r["status"] == "MAINTENANCE"
    assert "FR-T:control chart drift" in ids(r)


def test_critical_with_a_reading_outside_1_to_6_in_last_24h():
    df = make_fridge({"2026-10-20 01:00": 6.2})
    r = rules.fridge_status(df, "FR-T", good_log())
    assert r["status"] == "CRITICAL"
    assert "FR-T:reading outside 1-6 C" in ids(r)


def test_excursion_earlier_in_the_week_is_maintenance_not_critical():
    df = make_fridge({"2026-10-16 14:45": 6.4})
    r = rules.fridge_status(df, "FR-T", good_log())
    assert "FR-T:excursion to review" in ids(r)
    assert "FR-T:reading outside 1-6 C" not in ids(r)
    assert r["status"] in ("MAINTENANCE", "URGENT")


def test_urgent_with_reading_at_5_5_and_an_active_maintenance_alert():
    df = make_fridge()
    hour_at(df, "2026-10-20 02:00", 5.6)        # two hours at 5.6: drift alert + near the limit
    hour_at(df, "2026-10-20 04:00", 5.6)
    r = rules.fridge_status(df, "FR-T", good_log())
    assert r["status"] == "URGENT"
    assert "FR-T:temperature near the limit" in ids(r)


def test_near_limit_reading_alone_is_not_urgent():
    df = make_fridge({"2026-10-20 02:00": 5.6})  # one raw reading, no maintenance alert active
    r = rules.fridge_status(df, "FR-T", good_log())
    assert r["status"] != "URGENT" and r["status"] != "CRITICAL"


def test_urgent_when_unacknowledged_for_24h_then_not_after_acknowledge():
    log = good_log(**{"coil cleaning": "2026-07-10"})   # due 2026-10-08, more than 24 h ago
    r = rules.fridge_status(make_fridge(), "FR-T", log)
    assert r["status"] == "URGENT"
    alert = r["alerts"][0]
    assert alert["id"] == "FR-T:coil cleaning overdue" and alert["escalated"]

    ack = {"equipment_id": "FR-T", "service_date": "2026-10-20", "service_type": "acknowledge",
           "technician": "Sam", "notes": "FR-T:coil cleaning overdue"}
    log2 = rules.make_log(log.drop(columns="when"), [ack])
    r2 = rules.fridge_status(make_fridge(), "FR-T", log2)
    assert r2["status"] == "MAINTENANCE"
    assert r2["alerts"][0]["acknowledged"] and not r2["alerts"][0]["escalated"]


def test_acknowledge_before_the_alert_started_does_not_count():
    old_ack = {"equipment_id": "FR-T", "service_date": "2026-09-01", "service_type": "acknowledge",
               "technician": "Sam", "notes": "FR-T:coil cleaning overdue"}
    log = good_log(**{"coil cleaning": "2026-07-10"})
    log = rules.make_log(log.drop(columns="when"), [old_ack])
    assert rules.fridge_status(make_fridge(), "FR-T", log)["status"] == "URGENT"


def test_interval_alert_is_cleared_by_a_new_service_row():
    log = good_log(**{"coil cleaning": "2026-07-10"})
    assert "FR-T:coil cleaning overdue" in ids(rules.fridge_status(make_fridge(), "FR-T", log))
    yes = {"equipment_id": "FR-T", "service_date": "2026-10-20", "service_type": "coil cleaning",
           "technician": "Sam", "notes": "checklist yes"}
    log2 = rules.make_log(log.drop(columns="when"), [yes])
    r = rules.fridge_status(make_fridge(), "FR-T", log2)
    assert r["alerts"] == [] and r["status"] == "OK"


def test_rising_duty_power_loss_and_door_events():
    df = make_fridge()
    df.loc[df["timestamp"] > "2026-10-13 06:00", "compressor_duty"] = 50   # +10 points
    df.loc[df["timestamp"] == "2026-10-20 03:00", "power_on"] = 0
    df.loc[df["timestamp"].isin(pd.to_datetime(["2026-10-20 01:00", "2026-10-20 01:15"])),
           "door_open"] = 1
    r = rules.fridge_status(df, "FR-T", good_log())
    assert {"FR-T:compressor duty rising", "FR-T:power loss", "FR-T:door open"} <= set(ids(r))


def test_single_door_reading_is_not_an_event():
    df = make_fridge()
    df.loc[df["timestamp"] == "2026-10-20 01:00", "door_open"] = 1
    assert rules.fridge_status(df, "FR-T", good_log())["alerts"] == []


# ---------------------------------------------------------------------------
# Generators: weekly starts every 7 days from 2026-08-04 to 2026-10-20 (all passing)
# ---------------------------------------------------------------------------
def make_tests(last_row=None):
    dates = pd.date_range("2026-08-04", "2026-10-20", freq="7D")
    df = pd.DataFrame({"generator_id": "GEN-T", "test_date": dates.strftime("%Y-%m-%d"),
                       "test_type": "weekly start", "started": 1, "seconds_to_start": 6.0,
                       "battery_v": 13.0, "fuel_pct": 80, "output_stable": float("nan")})
    if last_row:
        df.loc[df.index[-1], list(last_row)] = list(last_row.values())
    return df


def gen_log():
    rows = [{"equipment_id": "GEN-T", "service_date": "2026-09-01",
             "service_type": "generator service", "technician": "x", "notes": ""},
            {"equipment_id": "GEN-T", "service_date": "2026-10-01",
             "service_type": "monthly load", "technician": "x", "notes": ""}]
    return rules.make_log(pd.DataFrame(rows, columns=rules.LOG_COLUMNS))


def test_generator_ok_when_all_tests_pass():
    r = rules.generator_status(make_tests(), "GEN-T", gen_log(), NOW)
    assert r["status"] == "OK", r["alerts"]


def test_generator_low_battery_is_a_ticket_and_clears_on_a_later_passing_test():
    bad = make_tests({"battery_v": 11.9})
    r = rules.generator_status(bad, "GEN-T", gen_log(), NOW)
    assert "GEN-T:low battery (weekly start)" in ids(r)
    later = pd.concat([bad, pd.DataFrame([{**bad.iloc[-1].to_dict(), "test_date": "2026-10-20",
                                           "battery_v": 13.1}])], ignore_index=True)
    later.loc[later.index[-2], "test_date"] = "2026-10-19"
    r2 = rules.generator_status(later, "GEN-T", gen_log(), NOW)
    assert "GEN-T:low battery (weekly start)" not in ids(r2)


def test_generator_failed_start_message_and_dead_time():
    bad = make_tests({"started": 0, "seconds_to_start": float("nan")})
    r = rules.generator_status(bad, "GEN-T", gen_log(), NOW)
    assert "GEN-T:failed start (weekly start)" in ids(r)
    assert r["status"] in ("MAINTENANCE", "URGENT")
    dead = [a for a in r["alerts"] if a["id"] == "GEN-T:dead-time risk"]
    assert dead and "a rough estimate" in dead[0]["message"] and "95% upper" in dead[0]["message"]


def test_generator_overdue_weekly_start():
    old = make_tests().iloc[:-3]                 # last weekly start 2026-10-06; now 2026-10-20
    r = rules.generator_status(old, "GEN-T", gen_log(), NOW)
    assert "GEN-T:weekly start overdue" in ids(r)


def test_attention_list_sorts_by_tier_then_id():
    tele = make_fridge()
    tele2 = tele.copy()
    tele2["unit_id"] = "FR-A"
    hour_at(tele2, "2026-10-20 02:00", 5.0)      # FR-A gets a WATCH
    both = pd.concat([tele, tele2], ignore_index=True)
    log = rules.make_log(pd.concat([good_log().drop(columns="when"),
                                    good_log().drop(columns="when").assign(equipment_id="FR-A"),
                                    gen_log().drop(columns="when")]))
    out = rules.attention_list(both, make_tests(), log)
    order = [(u["id"], u["status"]) for u in out["units"]]
    assert order == [("FR-A", "WATCH"), ("FR-T", "OK"), ("GEN-T", "OK")]


# ---------------------------------------------------------------------------
# Maintenance complete button (SPEC.md section 6, "How alerts clear")
# ---------------------------------------------------------------------------
def press(equipment_id, when, who="Sam"):
    """A 'maintenance complete' log row, as the page's button writes it."""
    return {"equipment_id": equipment_id, "service_date": when,
            "service_type": "maintenance complete", "technician": who, "notes": ""}


def bundled():
    """The real data/ files and the bundled log."""
    from pathlib import Path
    d = Path(__file__).resolve().parent.parent / "data"
    return (pd.read_csv(d / "fridge_telemetry.csv"), pd.read_csv(d / "generator_tests.csv"),
            pd.read_csv(d / "maintenance_log.csv", dtype=str, keep_default_na=False))


def units(att):
    return {u["id"]: u for u in att["units"]}


def test_bundled_log_alone_resolves_nothing():
    tele, tests, base = bundled()
    for u in rules.attention_list(tele, tests, rules.make_log(base))["units"]:
        assert u["resolved_alerts"] == [] and u["maintenance_complete_at"] is None
        assert not any(a["retriggered"] for a in u["alerts"])


def test_press_on_fr02_resolves_all_its_alerts():
    tele, tests, base = bundled()
    before = units(rules.attention_list(tele, tests, rules.make_log(base)))["FR-02"]
    after = units(rules.attention_list(
        tele, tests, rules.make_log(base, [press("FR-02", "2026-10-20T07:00:00")])))["FR-02"]
    assert after["alerts"] == [] and after["status"] == "TASKS COMPLETE"
    assert after["resolved_by"] == "Sam" and after["maintenance_complete_at"] == "2026-10-20T07:00:00"
    assert sorted(a["id"] for a in after["resolved_alerts"]) == sorted(a["id"] for a in before["alerts"])


def test_press_on_gen_b_resolves_including_todays_failed_start():
    tele, tests, base = bundled()
    before = units(rules.attention_list(tele, tests, rules.make_log(base)))["GEN-B"]
    after = units(rules.attention_list(
        tele, tests, rules.make_log(base, [press("GEN-B", "2026-10-20T07:00:00")])))["GEN-B"]
    assert after["alerts"] == [] and after["status"] == "TASKS COMPLETE"
    assert "GEN-B:failed start (weekly start)" in [a["id"] for a in after["resolved_alerts"]]
    assert len(after["resolved_alerts"]) == len(before["alerts"])


def test_complete_unit_sorts_with_ok_after_watch():
    tele, tests, base = bundled()
    att = rules.attention_list(tele, tests, rules.make_log(base, [press("FR-02", "2026-10-20T07:00:00")]))
    order = [u["status"] for u in att["units"]]
    assert order.index("WATCH") < order.index("TASKS COMPLETE")
    assert "URGENT" not in order[order.index("TASKS COMPLETE"):]


def test_fridge_flag_after_the_press_retriggers_and_before_it_does_not():
    df = make_fridge()
    hour_at(df, "2026-10-20 02:00", 5.0)             # flagged hour 02:00-03:00, last_seen 03:00
    early = rules.make_log(good_log().drop(columns="when"), [press("FR-T", "2026-10-20T02:30:00")])
    r = rules.fridge_status(df, "FR-T", early)
    assert ids(r) == ["FR-T:control chart watch"] and r["alerts"][0]["retriggered"] is True
    assert r["alerts"][0]["last_seen"] == "2026-10-20T03:00:00"
    late = rules.make_log(good_log().drop(columns="when"), [press("FR-T", "2026-10-20T04:00:00")])
    r2 = rules.fridge_status(df, "FR-T", late)
    assert r2["alerts"] == [] and r2["status"] == "TASKS COMPLETE"
    assert [a["id"] for a in r2["resolved_alerts"]] == ["FR-T:control chart watch"]


def test_failing_generator_test_after_the_press_retriggers():
    bad = make_tests({"started": 0, "seconds_to_start": float("nan")})     # failed start 2026-10-20
    log = rules.make_log(gen_log().drop(columns="when"), [press("GEN-T", "2026-10-13T07:00:00")])
    r = rules.generator_status(bad, "GEN-T", log, NOW)
    assert "GEN-T:failed start (weekly start)" in ids(r)
    assert all(a["retriggered"] for a in r["alerts"]) and r["status"] != "TASKS COMPLETE"
    same_day = rules.make_log(gen_log().drop(columns="when"), [press("GEN-T", "2026-10-20T07:00:00")])
    assert rules.generator_status(bad, "GEN-T", same_day, NOW)["alerts"] == []


# ---------------------------------------------------------------------------
# Task complete (per-alert button) and routine-task ticks (SPEC.md section 6)
# ---------------------------------------------------------------------------
def done(equipment_id, notes, when="2026-10-21T09:00:00", who="Sam"):
    """A 'task complete' log row, as the page writes it."""
    return {"equipment_id": equipment_id, "service_date": when, "service_type": "task complete",
            "technician": who, "notes": notes}


def fr02_after(*entries):
    tele, tests, base = bundled()
    return units(rules.attention_list(tele, tests, rules.make_log(base, list(entries))))["FR-02"]


def test_task_complete_resolves_only_that_alert():
    before = fr02_after()
    target = "FR-02:control chart drift"
    after = fr02_after(done("FR-02", target + ": Inspect the unit: door seal, coils, load and set point."))
    assert target not in [a["id"] for a in after["alerts"]]
    assert [a["id"] for a in after["resolved_alerts"]] == [target]
    assert len(after["alerts"]) == len(before["alerts"]) - 1
    assert after["status"] != "TASKS COMPLETE"
    assert after["resolved_by"] == "Sam"


def test_task_complete_on_overdue_service_clears_it_and_resets_days_since():
    before = fr02_after()
    assert before["days_since"]["coil cleaning"] == 102
    after = fr02_after(done("FR-02", "FR-02:coil cleaning overdue: Clean the condenser coils and log it."))
    assert after["days_since"]["coil cleaning"] == 0          # the row is after as_of: clamped to 0
    assert "FR-02:coil cleaning overdue" not in [a["id"] for a in after["alerts"]]
    assert [a["id"] for a in after["resolved_alerts"]] == ["FR-02:coil cleaning overdue"]
    assert after["days_since"]["probe calibration"] == before["days_since"]["probe calibration"]


def test_checklist_tick_resets_days_since_and_clears_the_coil_alert():
    after = fr02_after(done("FR-02", "FR-02: coil cleaning"))
    assert after["days_since"]["coil cleaning"] == 0
    assert "FR-02:coil cleaning overdue" not in [a["id"] for a in after["alerts"]]


def test_checklist_tick_of_a_service_that_is_not_overdue_resolves_nothing():
    tele, tests, base = bundled()
    fr01 = units(rules.attention_list(tele, tests, rules.make_log(base, [done("FR-01", "FR-01: coil cleaning")])))["FR-01"]
    assert fr01["days_since"]["coil cleaning"] == 0
    assert fr01["resolved_alerts"] == [] and fr01["status"] != "TASKS COMPLETE"


def test_all_alerts_cleared_by_task_complete_gives_tasks_complete():
    entries = [done("FR-02", a["id"] + ": " + a["task"]) for a in fr02_after()["alerts"]]
    after = fr02_after(*entries)
    assert after["alerts"] == [] and after["status"] == "TASKS COMPLETE"
    assert len(after["resolved_alerts"]) == len(entries)


def test_task_complete_followed_by_newer_evidence_retriggers():
    df = make_fridge()
    hour_at(df, "2026-10-20 02:00", 5.0)             # last_seen 03:00
    row = done("FR-T", "FR-T:control chart watch: Look at the chart again", "2026-10-20T02:30:00")
    r = rules.fridge_status(df, "FR-T", rules.make_log(good_log().drop(columns="when"), [row]))
    assert ids(r) == ["FR-T:control chart watch"] and r["alerts"][0]["retriggered"] is True
    row["service_date"] = "2026-10-20T04:00:00"
    r2 = rules.fridge_status(df, "FR-T", rules.make_log(good_log().drop(columns="when"), [row]))
    assert r2["alerts"] == [] and r2["status"] == "TASKS COMPLETE"


def test_plain_service_rows_count_as_before():
    plain = {"equipment_id": "FR-02", "service_date": "2026-10-20", "service_type": "coil cleaning",
             "technician": "Sam", "notes": ""}
    after = fr02_after(plain)
    assert after["days_since"]["coil cleaning"] == 0
    assert "FR-02:coil cleaning overdue" not in [a["id"] for a in after["alerts"]]

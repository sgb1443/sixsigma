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

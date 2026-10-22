# Test data and codebook

**All data here is INVENTED.** It is shaped like real field data so the tool can be tested, but no
real blood bank, unit, or person is behind it. Technician names are made-up first names or initials.

Regenerate everything (identical output every time, fixed seed 20261020), from the repo root:

```
python data/make_data.py
```

The script uses only the Python standard library. "As of" date for all checks: **2026-10-20**.
Times are local time, ISO 8601, no time zone.

There are exactly three CSV files per scenario (the codebook below applies to every scenario; see [Scenarios](#scenarios)).

## 1. `fridge_telemetry.csv`

**One row = one 15-minute reading from one refrigerator's data logger.** 4 units x 1344 readings
(14 days, 2026-10-06T06:15:00 to 2026-10-20T06:00:00) = 5376 rows, about 200 KB.

| Column | Type | Units | Allowed values |
|---|---|---|---|
| `unit_id` | text | none | FR-01, FR-02, FR-03, FR-04 |
| `timestamp` | text, ISO 8601 | local time | `YYYY-MM-DDTHH:MM:SS`, every 15 min |
| `temp_c` | number, 1 decimal | degrees C | about 2 to 7; **blank = missing reading** |
| `door_open` | integer | none | 0 = closed, 1 = open at the reading |
| `power_on` | integer | none | 0 = no mains power, 1 = power on |
| `compressor_duty` | integer | % of the 15-min interval the compressor ran | 0 to 100 |

The spec for red cell storage is 1-6 C.

### Planted stories

| Unit | Story | Exact events | What the tool should flag |
|---|---|---|---|
| FR-01 | Healthy. Mean about 4.0 C, small noise, 8 brief door openings with a small bump that recovers. Duty about 40%. | none | OK. High Cpk. |
| FR-02 | Drifting warm. In control for the first 10 days, then the mean creeps up and duty rises (about 40% to 56%). Last 3 days mean about 4.75 C vs about 4.01 C in the first 10 days; the temperature stays below 5.5 C. Also one short power loss. | Drift starts at reading 961 (2026-10-16T06:15:00) and runs to the end. Power loss: `power_on=0` at 2026-10-10T08:15, 08:30, 08:45, 09:00 (4 readings, duty 0, small temperature rise). | MAINTENANCE: run of 8+ above the centre line, rising compressor duty, lower Cpk. Immediate alert for the power loss. Also **overdue coil cleaning** (see below). |
| FR-03 | Door-seal problem. 30 door-open episodes, 19 lasting 2 or more consecutive readings (longest 4 readings, one hour), temperature spikes that recover, elevated duty (about 55%). | **CRITICAL test case:** door open 2026-10-16T14:00 to 14:45; temp_c = 5.0, 5.7, 6.1, 6.4 (peak at **2026-10-16T14:45:00**), then 6.0 at 15:00, 5.3 at 15:15, 4.7 at 15:30. Readings above 6.0: 14:30 (6.1) and 14:45 (6.4). All other spikes are capped at 5.8. | CRITICAL (reading outside 1-6 C). Immediate alert for door open > 5 min. |
| FR-04 | Stuck probe. Normal until the last 24 hours, then `temp_c` is exactly 4.0 for 96 readings in a row. Three missing readings. | Constant 4.0 from 2026-10-19T06:15:00 to 2026-10-20T06:00:00 (96 readings). Blank `temp_c` at 2026-10-08T08:15:00, 2026-10-12T14:45:00, 2026-10-16T16:15:00. | "No variation" (MR-bar = 0, Cpk undefined, no crash) on the last 24 h; missing readings handled without error; suggest probe check. |

The temperatures are generated with correlated noise (neighbouring readings are alike), which is how
real 15-minute logger data behaves; this is why I-MR limits are narrow (see `SPEC.md` section 4).

### How this would be collected for real

Most blood bank refrigerators have a built-in data logger or a monitoring system (a wireless probe
in a glycol bottle plus a door switch and a power sensor). The facilities team exports the log as
CSV, usually one file per unit per period, and the compressor-running percentage comes from the
unit's controller. Columns would need to be renamed to the ones above; the logger clock should be
set to local time. A missing reading happens when a probe loses its connection.

## 2. `generator_tests.csv`

**One row = one test of one backup generator.** 2 generators x 32 tests (26 weekly start tests, on
Tuesdays from 2026-04-28 to 2026-10-20, plus 6 monthly load tests, on Wednesdays) = 64 rows.

| Column | Type | Units | Allowed values |
|---|---|---|---|
| `generator_id` | text | none | GEN-A, GEN-B |
| `test_date` | date | none | `YYYY-MM-DD` |
| `test_type` | text | none | `weekly start` or `monthly load` |
| `started` | integer | none | 1 = started, 0 = failed to start |
| `seconds_to_start` | number, 1 decimal | seconds | about 3 to 17; **blank if it did not start** |
| `battery_v` | number, 2 decimals | volts | about 11.5 to 13.2; healthy 12.6-13.2, low below 12.2 |
| `fuel_pct` | integer | % of tank | 0 to 100 |
| `output_stable` | integer | none | 1 = stable, 0 = unstable; **blank for all weekly start tests (not measured, only the monthly load test measures it)**, and blank for a monthly test that did not start |

Monthly load test dates: 2026-05-13, 06-10, 07-08, 08-12, 09-09, 10-14.

### Planted stories

| Generator | Story | Exact events | What the tool should flag |
|---|---|---|---|
| GEN-A | Healthy. Always starts in 3-7 s, battery 12.7-13.2 V, fuel 63% or more. | none | OK. |
| GEN-B | Fine until late summer, then trouble. Battery falls steadily from 2026-08-25 (about 12.9 V down to 11.6 V). Slow starts (over 10 s). **Two failed starts in the last month.** | Slow starts: 2026-09-15 (10.8 s), 09-22 (12.5 s), 09-29 (14.1 s), 10-13 (16.8 s), monthly load 10-14 (13.5 s, `output_stable = 0`). **Failed starts: 2026-10-06 and 2026-10-20** (`started = 0`, battery 11.8 V). | MAINTENANCE ticket for slow start, low battery, failed start, unstable output. p-chart: 2 failures in about 4 tests that month (p = 0.5) should be above the UCL (p-bar over all 32 tests is 2/32, about 0.06). Test-interval risk above 1%. |

### How this would be collected for real

The facilities team already writes down each generator test: date, whether it started, a stopwatch
time to start, the battery voltage on the meter or controller display, and the fuel gauge. The
output stability check is done during the monthly load test (generator run under load, watch the
voltage and frequency). Entering these in a spreadsheet and saving as CSV gives this file.

## 3. `maintenance_log.csv`

**One row = one service done on one piece of equipment.** 17 rows.

| Column | Type | Units | Allowed values |
|---|---|---|---|
| `equipment_id` | text | none | FR-01 to FR-04, GEN-A, GEN-B |
| `service_date` | date | none | `YYYY-MM-DD` |
| `service_type` | text | none | `coil cleaning`, `probe calibration`, `full service`, `generator service` (generators only) |
| `technician` | text | none | invented first name or initials |
| `notes` | text | none | free text |

Intervals used by the tool (adjustable defaults): coil cleaning 90 days, probe calibration 180
days, full service 365 days. `SPEC.md` gives no interval for `generator service`, so none is applied.

### Days since last service, as of 2026-10-20

| Unit | Coil cleaning | Probe calibration | Full service |
|---|---|---|---|
| FR-01 | 69 | 153 | 336 |
| **FR-02** | **102 (OVERDUE)** | 127 | 320 |
| FR-03 | 49 | 173 | 271 |
| FR-04 | 84 | 168 | 231 |

Generators: GEN-A last serviced 2026-03-15 (219 days), GEN-B 2026-02-20 (242 days).
**Exactly one item is overdue: FR-02 coil cleaning** (last done 2026-07-10). FR-04 coil cleaning
(84 d) and FR-03 probe calibration (173 d) are close to their limits but still within interval.

### How this would be collected for real

The facilities team keeps a service log (a spreadsheet or a work-order system). Each completed job
is one row. In the tool, each "yes" on the maintenance checklist adds a row with today's date.

## Scenarios

The tool ships with four test scenarios. **The codebook above (every column, unit and allowed value) applies to every scenario**: each one has the same three files with the same columns and formats, the same 14-day window (2026-10-06T06:15:00 to 2026-10-20T06:00:00) and the same "now" (2026-10-20 06:00). Scenarios 1 to 3 are files made by `python data/make_data.py` (fixed seeds); scenario 1 is the main files above and never changes. Copies of all of them are in `app/data/` (a test checks they stay identical).

| # | Scenario (API key) | Folder | What is planted | What the tool should show |
|---|---|---|---|---|
| 1 | Mixed problems (`mixed`) | `data/` (the three files above) | The stories in the tables above. | FR-02 URGENT, FR-03 URGENT, GEN-B URGENT, FR-04 MAINTENANCE, FR-01 WATCH, GEN-A OK (see `demo/README.md`). |
| 2 | All healthy (`healthy`) | `data/all_healthy/` | 4 fridges at about 4.0 C (3.4 to 5.0 C all week), steady duty about 40%, power always on, brief single-reading door openings (none in the last 24 h), no stuck runs. Both generators start in 3-7 s, battery 12.7 V or more, fuel 62% or more, no failures. Weekly start tests on Tuesdays 2026-04-28 to 2026-10-20, monthly load tests as in scenario 1 (latest 2026-10-14). Every service within its interval (FR-02 coils cleaned 2026-09-02). | All six units **OK** (green). |
| 3 | Blackout day (`blackout`) | `data/blackout_day/` | Grid outage: `power_on = 0` for all 4 fridges from **2026-10-19T22:00:00 to 2026-10-20T01:45:00** (16 readings, duty 0); power is back at 02:00. Temperature climbs while off and peaks at the last off reading (01:45), then recovers: FR-02 **6.4 C** (above 6.0 from 01:00), FR-01 **5.9 C** (5.5 or more from 01:00), FR-03 5.1 C, FR-04 5.0 C. GEN-A: weekly start test on **2026-10-19** (Mondays this time) has `started = 0`, battery 11.8 V; GEN-B healthy. Before the outage everything is normal; services within interval. | FR-02 **CRITICAL** (move blood now); FR-01 **URGENT** (power-loss alert plus a reading of 5.5 C or more); FR-03 and FR-04 **MAINTENANCE** (power loss, hourly temperatures beyond the limits); GEN-A **URGENT** (failed start, low battery, p-chart signal, dead-time risk; escalated because the failure is over 24 h old and not acknowledged); GEN-B OK. |
| 4 | Random data (`random`) | none (made in memory by `app/simulate.py`) | See below. | See below. |

**Scenario 2 and the method's false alarms.** The control chart judges the latest week against limits from the first week, using hourly means of correlated readings, so even a perfectly healthy fridge sometimes gets a WATCH or even a drift alert from noise alone (a run of 8 hourly means on one side of the centre line). We did not change the method. The scenario-2 file is the first of the candidate seeds we tried that came out all OK; with the same generator, a few of the seeds we tried gave one stray WATCH or MAINTENANCE. To keep the demonstration clean, the healthy fridges have no door openings in the last 24 h: the chart flags the hour of every door opening (it heats that hour by about 0.3 C). If a stray WATCH ever appears on healthy data, that is the method's false-alarm rate, not a planted problem.

**Scenario 4: random data.** `random_scenario(seed)` in `app/simulate.py` builds the three tables from a random seed (the page picks one and shows it; "New random data" picks another). The same seed always gives the same data. Each fridge independently gets one behaviour: healthy, drifting warm (last 4 days, below 5.5 C), door-seal problem, stuck probe (last 24 h), or a power outage in the last 24 h (sometimes above 6 C). Each generator is healthy or has recent failed starts with low battery. About one in three seeds also has one overdue service. The API returns an **answer key** (plain-language list of what was planted per unit) with the attention list, and the page shows it, so you can compare it with the status bar. There are no fixed expected numbers. Known limit: the same false-alarm rate as above means a healthy fridge is sometimes flagged (tests/test_scenarios.py lists the seeds where that happened).

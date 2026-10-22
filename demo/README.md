# Demo: one walk through every test unit

Run `bash app/testme.sh`, open <http://localhost:8000>.

The page shows the attention list (most urgent first). Click **Open detail** on a unit for its chart and numbers. Every number below was fetched from the running API on the bundled data; "now" is the last timestamp in the data, 2026-10-20 06:00. The curl commands print the same JSON the page uses (add `| python -m json.tool` to read it).

Fridge chart: hourly mean temperature of the latest week, with limits from the baseline week. Red dot = beyond the limits, amber diamond = run of 8 on one side of the center line, shaded band = the 1-6 °C spec. Generator chart: one bar per month for the share of start tests that failed (grey = too few tests to judge, red with a triangle = signal).

This page walks through **Scenario 1: Mixed problems**, the default of the "Test dataset:" dropdown. The sections at the end show the other three scenarios.

## FR-01 (healthy unit)
Click path: **FR-01** row, **Open detail**. `curl http://localhost:8000/fridges/FR-01`
- Status **WATCH**: one hour beyond the baseline limits in the last 24 h (not an action).
- Center 4.013 °C, sigma 0.097, UCL 4.303, LCL 3.723. Cp 8.03, Cpk 6.40, Pp 4.98, Ppk 3.9680. Stuck probe: no (longest run 5). Readings outside 1-6 °C: none.
- Chart: a flat line around 4 °C with a few red dots just over the limits.
- How to read: WATCH is amber on the chart and needs no action; Ppk far above 1.33 means the unit holds 1-6 °C easily.

## FR-02 (drifting warm)
`curl http://localhost:8000/fridges/FR-02`
- Status **URGENT**. Alerts: "Coil cleaning overdue (last done 2026-07-10, interval 90 days)" and "Hourly temperature outside the baseline limits 3.69-4.31 C (24 hours beyond, run of 8: yes)". Both were active for more than 24 h without their task completed, so the tool raised them from MAINTENANCE to URGENT.
- Center 4.000, sigma 0.103, UCL 4.309, LCL 3.690. Cp 7.89, Cpk 5.14, Pp 2.05, **Ppk 1.3331** (just above the 1.33 line). 76 hours beyond the limits and 83 run-rule points (the two overlap; 84 flagged in all). Days since: coils 102, probe calibration 127, full service 320.
- Chart: the line climbs above the UCL and stays there, a long red stretch.
- How to read: the drift is caught by the control chart, not by Ppk; Cpk (5.14) looks fine only because 15-min readings are autocorrelated.

### Close the loop: press "Task complete" on each alert
On the page: each FR-02 alert has its own **Task complete** button next to its task; it asks "Done: <task>? Yes / Cancel". Real `POST /attention` output, the rows being what the page sends (technician Sam, 2026-10-21 09:00):
- Before: FR-02 **URGENT**; active `FR-02:coil cleaning overdue`, `FR-02:control chart drift`; `days_since` coil cleaning 102.
- Row 1: `FR-02, 2026-10-21T09:00:00, task complete, Sam, "FR-02:coil cleaning overdue: Clean the condenser coils and log it."` Result: the coil alert is resolved **and** `days_since["coil cleaning"]` is 0 (a task complete on an overdue-service alert also counts as that service done; the row is after "now", so days are clamped at 0). FR-02 is still URGENT because the drift alert is active.
- Row 2: `FR-02, 2026-10-21T09:00:00, task complete, Sam, "FR-02:control chart drift: Inspect the unit: door seal, coils, load and set point."` Result: FR-02 **TASKS COMPLETE**, `resolved_by` Sam, `maintenance_complete_at` 2026-10-21T09:00:00; active none; resolved `FR-02:control chart drift`, `FR-02:coil cleaning overdue`. The page shows each finished alert as a green ✓ DONE row and the FR-02 box turns green.
- A routine-task tick is the same kind of row: `FR-02, ..., task complete, Sam, "FR-02: probe calibration"` sets `days_since["probe calibration"]` to 0 (it was 127) and fills and greys the tick-box.
- Re-trigger: if the data flags again after that time (a new flagged hour, an out-of-spec or stuck reading, a door or power event, or a newer failing generator test), the alert comes back marked "re-triggered after a task was completed".

## FR-03 (door-seal problem)
`curl http://localhost:8000/fridges/FR-03`
- Status **URGENT**. Alerts: "2 reading(s) outside 1-6 C earlier this week, last at 2026-10-16T14:45:00" (URGENT, not completed for more than 24 h) and "Door open on 2 or more consecutive readings (over 5 min) in the last 24 h" (MAINTENANCE).
- Out-of-spec readings: 2026-10-16 14:30 = 6.1 °C and 14:45 = 6.4 °C.
- Center 4.094, sigma 0.153, UCL 4.554, LCL 3.634. Cp 6.85, Cpk 5.27, Pp 2.65, Ppk 2.0389. Days since coils 49.
- Chart: red dots (9 beyond the limits) and amber diamonds earlier in the week; calm in the last 24 h (no I-MR signal there).
- How to read: the capability numbers look healthy, yet the unit went above 6 °C; the out-of-spec list is the proof, which is why it is checked separately.

## FR-04 (stuck probe)
`curl http://localhost:8000/fridges/FR-04`
- Status **MAINTENANCE**. Alerts: "Check probe: readings are not changing." and a control-chart drift alert (0 hours beyond, run of 8: yes).
- Stuck probe: **yes**, longest run of identical readings 96 (24 h). Center 4.009, sigma 0.117, UCL 4.360, LCL 3.659. Cp 8.95, Cpk 7.13, Pp 4.87, Ppk 3.8844 (these look excellent, which is the trap).
- Chart: the last stretch is a perfectly flat line (17 run-rule points in the last 24 h).
- How to read: a flat line is not a calm fridge; check the probe before you trust any capability number.

## GEN-A (healthy generator)
Click path: **GEN-A**, **Open detail**. `curl http://localhost:8000/generators/GEN-A`
- Status **OK**, no alerts.
- p-chart: months 2026-04 to 2026-10, tests per month 1, 5, 6, 5, 5, 6, 4, failures 0 in every month. 2026-04 is greyed ("too few tests to judge"). Message: "no failures in the baseline: any failure is a signal", which here means no signal because there is no failure.
- Dead time: "no failures observed, estimate not available" (that is not 0 risk).
- How to read: flat bars at zero and nothing flagged; any future failure would show up red.

## GEN-B (recent failed start)
`curl http://localhost:8000/generators/GEN-B`
- Status **URGENT**. Alerts include: battery 11.58 V (limit 12.2 V) and slow start 13.5 s (limit 10 s) on the monthly load test of 2026-10-14, output not stable, "Start-failure rate in 2026-10 is above the p-chart limit", and MAINTENANCE for the failed weekly start of 2026-10-20, battery 11.8 V and dead-time risk 3.7% (95% upper 12.7%).
- p-chart: 2026-10 has 2 failures in 4 tests (p = 0.5), flagged red with a triangle; the baseline had no failures, so there are no limits and any failure is a signal.
- Dead time: failures N = 2, lambda 0.01099 per day, T = 7 days, U (exact) 0.0375, lambda*T/2 approximation 0.0385, U at the 95% upper lambda 0.1269, T_max 1.83 days. Caveat: "based on only 2 failures: a rough estimate".
- How to read: U above 1% means a standby generator that is tested weekly is unavailable about 3.7% of the time; the 12.7% figure is the pessimistic bound.

## Scenario 2: All healthy
Pick **Scenario 2: All healthy** in the dropdown (or `curl "http://localhost:8000/attention?scenario=healthy"`). Real output, statuses per unit: FR-01 OK, FR-02 OK, FR-03 OK, FR-04 OK, GEN-A OK, GEN-B OK, with no alerts at all. The status bar is all green. See the note in [data/README.md](../data/README.md#scenarios) on why a healthy fridge can still get a stray WATCH.

## Scenario 3: Blackout day
Pick **Scenario 3: Blackout day** (`curl "http://localhost:8000/attention?scenario=blackout"`). Grid outage 2026-10-19T22:00 to 2026-10-20T01:45; GEN-A failed its weekly start test on 2026-10-19. Real output:
- FR-02 **CRITICAL**: reading outside 1-6 C (peak 6.4 C at 01:45), temperature near the limit, control chart drift, power loss. Move the blood now.
- FR-01 **URGENT**: temperature near the limit (peak 5.9 C), control chart drift, power loss.
- GEN-A **URGENT**: failed start (weekly start), low battery, p-chart signal, dead-time risk (URGENT only because the failure is over 24 h old without its task completed; completing its task clears it).
- FR-03 **MAINTENANCE** and FR-04 **MAINTENANCE**: control chart drift, power loss (peaks 5.1 and 5.0 C).
- GEN-B **OK**.
Every fridge has a "power loss" alert, so the status bar shows two red fridges, one red generator, two yellow fridges and one green generator.

## Scenario 4: Random data
Pick **Scenario 4: Random data**; the page shows "Seed N" and a "New random data" button, and an **Answer key** box. With a fixed seed (`curl "http://localhost:8000/attention?scenario=random&seed=2026"`) the data is always the same. Real output for seed 2026:
- Answer key: FR-01 healthy (also probe calibration overdue, 213 days); FR-02 stuck probe (from 2026-10-19 06:15); FR-03 door-seal problem (door open 3 readings in a row at 2026-10-19 18:45); FR-04 door-seal problem (2 readings at 20:45); GEN-A healthy; GEN-B failed to start at the weekly test on 2026-10-20.
- Tool: FR-01 **URGENT** (probe calibration overdue, plus a control chart drift alert on a healthy fridge: the method's false-alarm rate, see data/README.md), FR-02 **MAINTENANCE** (stuck probe, drift), FR-03 **MAINTENANCE** (door open), FR-04 **MAINTENANCE** (door open, drift), GEN-B **MAINTENANCE** (failed start, low battery, p-chart signal, dead-time risk), GEN-A OK.

## The log
Everything you click is kept in the **Log** panel. **Download log CSV** saves it (`equipment_id,service_date,service_type,technician,notes`); **Upload log CSV** loads it back and replaces the entries. The server stores nothing.

## Moving blood (Scenario 3: Blackout day, checked in a browser)
1. Pick **Scenario 3: Blackout day**. FR-02 is **CRITICAL** (red), FR-01 URGENT (red), FR-03 and FR-04 MAINTENANCE (yellow); every fridge box says **IN USE**.
2. FR-02 shows **Move FR-02's blood to FR-03** (no fridge is green, so the first yellow one). Confirm: FR-02 becomes **EMPTY: blood in FR-03**, FR-03 **IN USE · also holding FR-02's blood**. The log gets `blood moved | FR-02 -> FR-03`.
3. Press **Task complete** on each of FR-02's four alerts: each turns into a green ✓ DONE row, and FR-02 becomes **✓ TASKS COMPLETE** (green).
4. **All tasks complete: move blood back from FR-03 to FR-02** appears. Press it: both are **IN USE** again; the log gets `blood returned | FR-03 -> FR-02`.

## Download a dataset
Under the dataset dropdown, the three links download the selected scenario's input files, e.g. `http://localhost:8000/data/fridge_telemetry.csv?scenario=blackout`, or for Scenario 4 `...?scenario=random&seed=2026` (the generated week for that seed).

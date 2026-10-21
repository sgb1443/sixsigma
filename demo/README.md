# Demo: one walk through every test unit

Run `bash app/testme.sh`, open <http://localhost:8000>.

The page shows the attention list (most urgent first). Click **Open detail** on a unit for its chart and numbers. Every number below was fetched from the running API on the bundled data; "now" is the last timestamp in the data, 2026-10-20 06:00. The curl commands print the same JSON the page uses (add `| python -m json.tool` to read it).

Fridge chart: hourly mean temperature of the latest week, with limits from the baseline week. Red dot = beyond the limits, amber diamond = run of 8 on one side of the center line, shaded band = the 1-6 °C spec. Generator chart: one bar per month for the share of start tests that failed (grey = too few tests to judge, red with a triangle = signal).

## FR-01 (healthy unit)
Click path: **FR-01** row, **Open detail**. `curl http://localhost:8000/fridges/FR-01`
- Status **WATCH**: one hour beyond the baseline limits in the last 24 h (not an action).
- Center 4.013 °C, sigma 0.097, UCL 4.303, LCL 3.723. Cp 8.03, Cpk 6.40, Pp 4.98, Ppk 3.9680. Stuck probe: no (longest run 5). Readings outside 1-6 °C: none.
- Chart: a flat line around 4 °C with a few red dots just over the limits.
- How to read: WATCH is amber on the chart and needs no action; Ppk far above 1.33 means the unit holds 1-6 °C easily.

## FR-02 (drifting warm)
`curl http://localhost:8000/fridges/FR-02`
- Status **URGENT**. Alerts: "Coil cleaning overdue (last done 2026-07-10, interval 90 days)" and "Hourly temperature outside the baseline limits 3.69-4.31 C (24 hours beyond, run of 8: yes)". Both were active for more than 24 h with no acknowledgement, so the tool raised them from MAINTENANCE to URGENT.
- Center 4.000, sigma 0.103, UCL 4.309, LCL 3.690. Cp 7.89, Cpk 5.14, Pp 2.05, **Ppk 1.3331** (just above the 1.33 line). 76 hours beyond the limits and 83 run-rule points (the two overlap; 84 flagged in all). Days since: coils 102, probe calibration 127, full service 320.
- Chart: the line climbs above the UCL and stays there, a long red stretch.
- How to read: the drift is caught by the control chart, not by Ppk; Cpk (5.14) looks fine only because 15-min readings are autocorrelated.

### Walkthrough: acknowledge, then tick the checklist
Each step is a real `POST /attention` with the entries the page would send. On the page: click **Acknowledge** under an FR-02 alert, then open FR-02 and tick "coil cleaning: done, yes".
1. No entries: FR-02 is **URGENT**, with "coil cleaning overdue" (URGENT) and "control chart drift" (URGENT).
2. Acknowledge the coil alert (a log row `FR-02, 2026-10-20T08:00:00, acknowledge, manager, "FR-02:coil cleaning overdue"`): the coil alert drops to **MAINTENANCE** and shows "acknowledged by manager"; FR-02 is still **URGENT** because the drift alert is not acknowledged.
3. Acknowledge the drift alert too: FR-02 drops to **MAINTENANCE**. The drift alert stays (acknowledging says "seen, on it" and does not clear a data alert).
4. Tick "coil cleaning: done, yes" (row `FR-02, 2026-10-20T08:05:00, coil cleaning, manager`): the coil alert is gone. Only "control chart drift" remains, until the data recovers.

Real output of step 4: `MAINTENANCE [('control chart drift', 'MAINTENANCE', True)]`. After step 4 the full list is FR-03 URGENT, GEN-B URGENT, FR-02 MAINTENANCE, FR-04 MAINTENANCE, FR-01 WATCH, GEN-A OK.

## FR-03 (door-seal problem)
`curl http://localhost:8000/fridges/FR-03`
- Status **URGENT**. Alerts: "2 reading(s) outside 1-6 C earlier this week, last at 2026-10-16T14:45:00" (URGENT, unacknowledged for more than 24 h) and "Door open on 2 or more consecutive readings (over 5 min) in the last 24 h" (MAINTENANCE).
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

## The log
Everything you click is kept in the **Log** panel. **Download log CSV** saves it (`equipment_id,service_date,service_type,technician,notes`); **Upload log CSV** loads it back and replaces the entries. The server stores nothing.

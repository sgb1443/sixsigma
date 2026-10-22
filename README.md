# Blood bank equipment monitor

A morning check for the blood bank's **quality/facilities manager**: which fridges and backup
generators need maintenance or inspection today, and does any blood need to move now.

- **Prompt answered:** Health Care Systems: Blood Banks (fridges fail, generators do not start in a blackout).
- **Team:** HW team 11
- Live: (Posit Connect link pending: the course server key has not been issued yet)

## Quick start

```bash
git clone https://github.com/sgb1443/sixsigma.git
cd sixsigma
python -m pip install -r app/requirements.txt
bash app/testme.sh          # then open http://localhost:8000
```

- Windows: use Git Bash for `bash app/testme.sh`, or run `cd app && python -m uvicorn main:app --port 8000`.
- Tests: `python -m pytest tests` (111 tests pass).
- Node is only needed to rebuild the page: `cd frontend && npm install && npm run build` (output goes to `app/static/`, which is committed).

## Using the page

| Part | What it does |
|---|---|
| Test dataset | The "Test dataset:" dropdown at the top switches between five options (see [Test datasets](#test-datasets)); option 5 is **Upload your own data**. Each one keeps its own log in the browser. |
| Status bar | One box per unit. Green = working (OK, WATCH, TASKS COMPLETE), yellow = MAINTENANCE, red = URGENT or CRITICAL (a CRITICAL fridge also says "move blood"). Click a box to pin that unit to the top of the list and open its detail there; click it again to undo. |
| Attention list | Every unit, most urgent first, each with a recommended task. Start at the top and work down. "Now" is the last timestamp in the data. |
| Open detail | Opens right under the unit's row (one at a time; the button then reads "Close detail"). Fridge: I-MR chart (red = beyond limits, amber = run of 8, shaded 1-6 °C band). Generator: monthly p-chart and the dead-time block. Plus days since each service and number tiles. Every chart has hover tooltips and a "Show as table" view. |
| Number tiles | Tinted with a word. Temperatures: green inside 1.5-5.5 °C, yellow at or beyond 1.5 / 5.5 °C, red outside 1-6 °C. Cp, Cpk, Pp, Ppk: green ≥ 1.33, yellow 1.00-1.33, red < 1.00. Dead-time U: green ≤ 1%, yellow above. T_max: green if at least the test interval. Failures N: yellow if any. Sigma has no colour. |
| Finished tasks | A completed alert stays listed as a green **✓ DONE** row with who and when; the unit's colour is recalculated from what is left (red → yellow → green). |
| Move blood | Only **CRITICAL FAILURE** (a reading outside 1-6 °C) needs a blood move: on that alert the Task complete button is a red **Move FR-02's blood to FR-04** (a green fridge, else yellow, else "a backup fridge"), which completes the alert. The fridge shows **EMPTY**, the backup **IN USE · also holding FR-02's blood**. When all the fridge's tasks are complete: "All tasks complete: move blood back". Every fridge box shows IN USE or EMPTY. URGENT MAINTENANCE near the limit asks to check the unit and have a backup ready, with a normal Task complete. |
| Download data | Under the dropdown, one link: the selected dataset as **one combined CSV sheet** (`all_data.csv`: readings, generator tests and service rows; the random week as generated, for its seed). |
| Upload your own data | Option 5: "Download CSV template" and an "Upload your CSV" button. Send one combined sheet (any dataset's download, edited, works); the page shows the units it found, rows per type and the as-of time, then runs the same statistics and rules on it. The file stays in this browser tab and in server memory only. An error (a missing column, a bad value, under 14 days of readings) names the row and column. |
| Task complete | One button per alert, next to its task. After a confirm ("Done: <task>? Yes / Cancel") it logs who and when and resolves that one alert (if its evidence is no newer than that moment). On an overdue-service alert it also counts as that service done today (days since = 0). When every alert of a unit is done, the unit shows TASKS COMPLETE. If the data flags again afterwards, the alert returns marked "re-triggered after a task was completed". |
| Routine tasks | Tick-boxes per unit; each tick is logged as a task complete ("FR-03: coil cleaning"), counts as that service done today, and clears its overdue alert. |
| Log | Download the entries made on this page as CSV. The log is kept **for this visit only**: reopening the page resets every click (the API stores nothing), so download it to keep a record. The service rows of an uploaded sheet are the maintenance history; the log panel is separate. |

Walkthrough of every test unit with real output: [demo/README.md](demo/README.md).

## Test datasets

The **Test dataset:** dropdown on the page (and the `scenario` parameter of the API) picks one of five scenarios. All use the same three file formats and the same 14-day window ending 2026-10-20 06:00. Details, exact timestamps and what each unit should show: [data/README.md](data/README.md#scenarios).

| Scenario | What it shows |
|---|---|
| 1: Mixed problems (default) | One healthy fridge, one drifting warm, one with a door-seal problem, one stuck probe; one healthy generator, one with failed starts. |
| 2: All healthy | Nothing wrong. Every unit is OK. |
| 3: Blackout day | A grid outage in the last 24 h hits all four fridges (FR-02 goes above 6 C: CRITICAL) and GEN-A failed its weekly start test the day before. |
| 4: Random data | Generated from a random seed shown on the page ("New random data" picks another). The same seed always gives the same data. An **answer key** lists what was planted in each unit so you can compare it with the status bar. There are no fixed expected numbers. |
| 5: Upload your own data | Your own CSV in the combined format. Every dataset above downloads as that one sheet, and there is a template; see [Combined sheet](data/README.md#combined-sheet-one-csv-with-everything). Any unit names and counts; each fridge needs 14 days of readings. |

## Inputs

Three CSVs, all **invented** test data. Full codebook: [data/README.md](data/README.md).

| File (one row =) | Key columns |
|---|---|
| `fridge_telemetry.csv` (a 15-min reading) | `unit_id`, `timestamp`, `temp_c` (°C), `door_open` (0/1), `power_on` (0/1), `compressor_duty` (% of interval) |
| `generator_tests.csv` (a test) | `generator_id`, `test_date`, `test_type` (weekly start / monthly load), `started` (0/1), `seconds_to_start` (s), `battery_v` (V), `fuel_pct` (%), `output_stable` (0/1) |
| `maintenance_log.csv` (a service) | `equipment_id`, `service_date`, `service_type`, `technician`, `notes` |

## The statistics

All live in `app/qc.py` (plain functions, no UI code); escalation tiers are in `app/rules.py`.

**1. Fridge I-MR chart** (`hourly_means`, `imr`). Average each hour's four readings (an hour counts only if all 4 fall in the week and at least 3 are valid). Sigma = MR-bar / 1.128, limits = center ± 3 sigma. Limits come from the **baseline week** (days 1-7); the latest week (days 8-14) is judged against them. Signals: a point beyond the limits, or a run of 8 on one side of the center.
Known answer: 4.0, 4.2, 3.9, 4.1, 4.3 gives mean 4.10, MR-bar 0.225, sigma 0.1995, UCL 4.698, LCL 3.502.

**2. Capability** (`capability`), raw readings of the last 7 days against the 1-6 °C spec.

| Index | Formula | Sigma used |
|---|---|---|
| Cp, Cpk | (USL − LSL) / 6σ, min(USL − mean, mean − LSL) / 3σ | within: MR-bar / 1.128 |
| Pp, Ppk | same formulas | overall sample standard deviation (n − 1) |

Known answer (same five numbers): Cp 4.18, Cpk 3.18, overall s 0.15811, Pp 5.27, Ppk 4.006.
**The flag is Ppk < 1.33**, not Cpk: neighbouring 15-min readings are nearly identical, so MR-bar understates the spread. On the test data Cpk is 5-9 on every unit, even FR-03 with readings at 6.4 °C. Constant data (MR-bar = 0) returns "no variation", not a crash.

**3. Stuck probe** (`stuck_probe`). Flag "check probe" if the raw temperature is identical for 12 or more readings in a row (3 h), or MR-bar = 0 over the last 24 h.

**4. Generator p-chart** (`p_chart`, `generator_summary`). Each weekly start and monthly load test is one trial; failure = did not start. p-bar comes from the **baseline months** (all months before the latest). sigma_p = sqrt(p-bar (1 − p-bar) / n), limits p-bar ± 3 sigma_p, LCL floored at 0. Months with fewer than 3 tests are greyed ("too few tests to judge") and left out of p-bar. If the baseline has no failures there are no limits: "any failure is a signal".
Known answer: p-bar 0.04, n = 4 gives sigma_p 0.0980, UCL 0.334, LCL 0.

**5. Generator dead time** (`dead_time`, `poisson_upper`). Chance a standby generator is down when needed. λ = failures / days in service (per day); T = test interval (7 d for weekly start tests); days in service = (last test − first test) + 7.
U = 1 − (1 − e^(−λT)) / (λT), with the approximation λT / 2 shown beside it. Flag if U > 0.01. T_max is the longest test interval that keeps U ≤ 0.01: the tool solves U(T) = 0.01 exactly by bisection (GEN-B: 1.83 days) and also reports the approximation 2 × 0.01 / λ. The failure count N is always shown. When N < 5 the tool says "based on only N failures: a rough estimate" and also shows U at the exact 95% Poisson upper limit λ_U = μ_U / days (μ_U found by bisection). The flag stays on the point estimate.
Known answers: λ = 0.001, T = 30 gives λT/2 = 0.015, U = 0.014851. μ_U is 3.6889 (N = 0), 5.5716 (N = 1), 7.2247 (N = 2). GEN-B: N = 2, 182 days gives λ 0.010989, U 0.037494; λ_U 0.039696, upper U 0.126914. Zero failures: "no failures observed, estimate not available", not zero risk.

## Assumptions and limits

- **Autocorrelated data.** 15-min readings are not independent. In our design check (limits from the same week), the healthy FR-01 gave 41 signals in a week on raw readings and 5 on hourly means, which is why the chart uses hourly means. With the final baseline-week limits, FR-01 still shows 7 signal hours in the latest week and 1 in the last 24 h, so it reads WATCH rather than OK: a few false alarms remain. Raw-reading Cpk is still optimistic, so the flag uses Ppk.
- **p-chart has small n** (about 4 tests a month): np is far below 5, limits are approximate, and one failure (0.25) cannot pass the UCL (0.334). So every single failed start is also ticketed directly.
- **λ comes from few failures**: a rough estimate, hence the upper-limit U beside it. Generator tests are assumed independent with a constant failure rate.
- Fridge readings are assumed roughly normal.
- All data is invented. Thresholds and intervals are adjustable defaults.

## Alert tiers

On the page, MAINTENANCE reads **ROUTINE MAINTENANCE** (yellow), URGENT reads **URGENT MAINTENANCE** (red) and CRITICAL reads **CRITICAL FAILURE** (red); the API and the table below use the short codes.

| Tier | When |
|---|---|
| WATCH | Exactly one beyond-limit hour in the last 24 h, no run of 8. Not an action. |
| MAINTENANCE | Drift (run of 8, or 2+ beyond-limit hours in 24 h), duty up 10+ points vs baseline, Ppk < 1.33, stuck probe, a reading outside 1-6 °C earlier in the week, overdue service (coils 90 d, probe 180 d, full 365 d), power loss or door open > 5 min. Generators: failed start, start > 10 s, battery < 12.2 V, fuel < 50%, unstable output, p-chart signal, overdue test (weekly 7 d, monthly load 30 d, service 365 d), U > 1%. |
| URGENT | A MAINTENANCE alert plus a raw reading in the last 24 h at ≥ 5.5 °C or ≤ 1.5 °C (check the unit, have a backup ready; no blood move); or any alert active over 24 h without its task completed. Swap the unit, move blood to backup. |
| CRITICAL | Any raw reading in the last 24 h outside 1-6 °C. Move blood now. |

**How alerts clear:** service-interval alerts by a routine-task tick or Task complete; data alerts on their own when the data returns to normal (I-MR: no signal in the last 24 h); generator test alerts on the next passing test; any single alert by **Task complete** (until re-triggered). A unit with at least one task completed and nothing active shows TASKS COMPLETE.

## API

Interactive docs at `/docs` (e.g. http://localhost:8000/docs). Details: [app/README.md](app/README.md).

| Endpoint | Returns |
|---|---|
| `GET /` | The dashboard page |
| `GET /health` | `{"status": "ok"}` |
| `GET /fridges/{unit_id}?as_of=` | `fridge_summary` plus `status`, `alerts`, `days_since` |
| `GET /generators/{gen_id}?as_of=` | `generator_summary` plus `status`, `alerts`, `days_since` |
| `GET /attention?as_of=` | All 6 units, most urgent first (bundled log) |
| `POST /attention` | Same, with body `{"log": [entries], "as_of": optional}`; entries use the `maintenance_log.csv` columns |
| `GET /log` | The bundled maintenance log |
| `GET /data/all_data.csv?scenario=` | The dataset as one combined CSV sheet |
| `GET /data/template.csv` | The format template (a format example, too few rows to analyse) |
| `POST /upload` | Body = the combined CSV text (up to 10 MB); returns `{id, units, counts, as_of}`; then use `scenario=upload&upload_id=<id>` on the other endpoints |

Unknown unit: 404. Bad log entry: 422. Bad `as_of`: 400.

## Repo map

| Path | What |
|---|---|
| `app/` | `qc.py` (statistics), `rules.py` (tiers), `main.py` (API), `static/` (built page), `data/` (bundled CSV copies), `testme.sh`, `manifestme.sh`, `deployme.sh` |
| `frontend/` | React + Vite source of the page |
| `data/` | Invented CSVs, `make_data.py`, codebook |
| `demo/` | Walkthrough of every test unit |
| `tests/` | pytest suite (known answers and degenerate cases) |
| `docs/` | Hackathon event pages |

## Credits

- Built with [FastAPI](https://fastapi.tiangolo.com/), [React](https://react.dev/), [Vite](https://vitejs.dev/) and [pandas](https://pandas.pydata.org/).
- Control-chart constants d2 = 1.128 and D4 = 3.267; method from the course textbook, https://timothyfraser.com/sigma/.
- Dead time follows the IEC 61508 PFDavg idea (λ × T / 2).
- Built with Claude Code as the coding agent, steered by the team.

This repo started from the Cornell Six Sigma Hackathon template (timothyfraser/sixsigmahackathon); event info in `docs/`.

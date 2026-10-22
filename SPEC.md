# SPEC: Six Sigma Hackathon team project

What your tool IS. Your agent reads this before every task. Where the code and this file
disagree, the code is the bug: change this file first, then the code.

Most of this page is already decided by the event and the [rubric](docs/criteria.md).
**Only the `FILL IN` lines change, and you do not have to write them by hand:** describe your app and ask your agent to update them. They depend on the prompt your team picks at kickoff
(prompts are secret until then, see [`docs/prompts.md`](docs/prompts.md)) and on your team.
The examples after each `FILL IN` show the shape of an answer. They are not hints about the prompts.

## 1. Team and prompt
- **Team name:** HW team 11
- **Track:** Virtual DL Challenge (7 days), due Fri Oct 23, 3 PM ET (12 PM Pacific). See [`docs/schedule.md`](docs/schedule.md).
- **Prompt we chose:** Health Care Systems: Blood Banks. "A regional blood bank loses thousands of dollars of supply when older refrigeration units fail or backup generators don't start during blackouts. Design a tool to track blood bank supply integrity and flag/predict failures."
- **Our public repo:** github.com/sgb1443/sixsigma

## 2. User and use case (rubric: scope 5, user and use case 5)
- **User:** the blood bank's quality/facilities manager, who reviews fridge and generator status each morning.
- **Decision the tool helps them make, and when:** each morning, which refrigeration units and backup generators need maintenance or inspection today, and whether blood must be moved to a backup unit right now.
- **Why this matches the prompt:** it covers both failure paths in the prompt (fridges failing, generators not starting) and flags problems before supply is lost.

## 3. Inputs (rubric: minimal inputs 5)
Only what this user can actually measure or already knows. Fewer is better; every input a user
cannot measure costs points.

| Input | Type and units | Where the user gets it |
|---|---|---|
| `fridge_telemetry.csv`, one row per reading (every 15 min) | unit_id; timestamp; temp_c (°C); door_open (0/1); power_on (0/1); compressor_duty (% of interval running) | exported from each unit's built-in data logger / monitoring system |
| `generator_tests.csv`, one row per test | generator_id; test_date; test_type (weekly start / monthly load); started (0/1); seconds_to_start (s); battery_v (V); fuel_pct (%); output_stable (0/1) | the facilities team's generator test records |
| `maintenance_log.csv`, one row per service | equipment_id; service_date; service_type; technician; notes | the maintenance log the facilities team already keeps; new entries come from the on-screen yes/no checklist |

**Combined sheet (download and upload).** The three inputs can also travel as one CSV, one row per record: `record_type` (reading / generator_test / service), `equipment_id`, `timestamp` (reading time, test date or service date), then the reading columns (`temp_c`, `door_open`, `power_on`, `compressor_duty`), the test columns (`test_type`, `started`, `seconds_to_start`, `battery_v`, `fuel_pct`, `output_stable`) and the service columns (`service_type`, `technician`, `notes`); a row fills only the columns of its type. Every dataset in the dropdown downloads as this one sheet, and option 5 "Upload your own data" takes it (with a downloadable template). Uploaded data is analysed with the same functions and rules, kept in that browser tab only, never stored by the server. Readings must cover at least 14 days (baseline week + latest week); generator and service rows are optional; any unit names and counts work.

## 4. The statistics, and why (rubric: valid QC analysis 25)
Say the method in words before any code exists. Load the `stats-first-steering` skill first.
- **Method:**
  - Fridges: I-MR control chart on **hourly mean** temperature (the average of each hour's four 15-min readings; an hour is used only if all 4 of its readings fall inside the week and at least 3 are valid, so partial hours at the week edges are dropped), with sigma = MR-bar / 1.128 (d2 for n = 2) and limits at center ± 3 sigma. Limits come from a **baseline week** (days 1-7) and the latest week (days 8-14) is judged against them (Phase I / Phase II). Rules: a point beyond the limits, or a run of 8 on one side of the center line. Spec check of every raw reading against 1-6 °C (red cell storage). Capability on the raw readings of the last 7 days against the 1-6 °C spec, two indices shown side by side: Cp and Cpk with within sigma = MR-bar / 1.128, and Pp and Ppk with the overall sample standard deviation (n − 1). Flag if **Ppk** < 1.33. Why Ppk: consecutive 15-min readings are nearly identical, so MR-bar understates the spread; on the test data Cpk was 5-9 on every unit, even FR-03 with readings at 6.4 °C, while Ppk puts the drifting FR-02 at 1.3331, just above the line (its drift is caught by the I-MR alert instead).
  - Stuck-probe check: flag "check probe" when the raw temperature is identical for ≥ 12 readings in a row (3 h), or MR-bar = 0 over the last 24 h.
  - Generators: p-chart of the start-test failure rate per month (every weekly start and monthly load test is one trial; failure = did not start). p-bar comes from the **baseline months** (all months before the latest), not including the month being judged (Phase I / II, as for the fridges). If the baseline has 0 failures, there are no limits, and the chart says "no failures in the baseline: any failure is a signal". Months with fewer than 3 tests are greyed out ("too few tests to judge") and left out of p-bar. p-bar with 3-sigma limits, LCL floored at 0. It is the trend view; every single failed start also raises a ticket on its own.
  - Generator dead-time check (fractional dead time of a periodically tested standby unit with a constant failure rate, same idea as IEC 61508 PFDavg = lambda_DU × T / 2): lambda = failures found at tests / total days in service (per day); T = test interval in days (7 for weekly start tests). Exact average unavailability U = 1 − (1 − exp(−lambda·T)) / (lambda·T), with the approximation lambda·T / 2 shown alongside (good when lambda·T is small). Flag if U > 0.01 and report the longest interval that keeps U ≤ 0.01 (T_max ≈ 2 × 0.01 / lambda). Days in service = (last test date − first test date) + 7. Uncertainty: always report the failure count N next to lambda; when N < 5, add the caveat "based on only N failures: a rough estimate", and also show U at the exact 95% upper limit of lambda (lambda_U = mu_U / days, where mu_U solves P(X ≤ N; mu_U) = 0.025 for a Poisson count, found by bisection). The flag stays on the point estimate.
- **Why it fits the use case:** fridge temperature is logged as single readings over time, so an individuals (I-MR) chart fits, and Cpk shows whether a unit can even hold the 1-6 °C band; generator tests are pass/fail, so a p-chart fits.
- **Assumptions and how the tool checks them:** fridge readings roughly normal (histogram in the demo). 15-min readings are autocorrelated: on raw readings the healthy test unit FR-01 gave 41 false signals in a week, and on hourly means it gave 5, which is why the chart uses hourly means. The raw-reading Cpk is still somewhat optimistic for the same reason; the README says so. Generator tests are assumed independent with a constant failure rate (exponential) for the dead-time check. The p-chart has n ≈ 4 tests a month, so np is far below 5 and its limits are approximate; one failure in a month cannot signal (0.25 < UCL 0.334), which is why single failures are ticketed directly.
- **Known-answer check:**
  - I-MR: temps 4.0, 4.2, 3.9, 4.1, 4.3 °C → mean 4.10, MR-bar 0.225, sigma 0.1995, UCL 4.698, LCL 3.502, Cp 4.18, Cpk 3.18; overall s = sqrt(0.10 / 4) = 0.15811, Pp 5.27, Ppk 4.006.
  - p-chart: p-bar 0.04 with n = 4 tests per month → sigma_p 0.0980, UCL 0.334, LCL 0.
  - Dead time: lambda = 0.001 per day, T = 30 days → lambda·T / 2 = 0.015, exact U = 0.014851.
  - Poisson 95% upper limit mu_U: N = 0 → 3.6889, N = 1 → 5.5716, N = 2 → 7.2247 (standard table values). GEN-B: N = 2, 182 days → lambda 0.010989, U 0.037494; lambda_U = 7.2247 / 182 = 0.039696, upper U 0.126914.
  - Degenerate cases: zero generator failures (lambda = 0: report "no failures observed, estimate not available", not 0 risk; no division by zero); constant temperatures (MR-bar = 0, Cpk undefined, returned as "no variation" not a crash), n = 1, and missing readings.

Fixed for every team:
- The statistic lives in **one plain, tested function** (`app/qc.py` or `app/qc.R`). The API, app or
  package only calls it and displays the result. No statistics inside UI code.
- Every statistic has a test against a known answer and a degenerate case (constant data, n = 1, missing values).
- You can explain every number the tool prints. If you cannot, it is not done.

## 5. Delivery (rubric: working tool 25)
- **Stack:** REST API (FastAPI, Python) with a React Vite dashboard front end. The RStudio project file in the repo is not used.
- **Starting demo:** `demos/fastapi/` copied into `app/`, with the front end from `demos/reactfront/`.

Fixed for every team:
- Start from **one** demo in `demos/`, copied to `app/`. Keep its four-script contract
  (`testme`, `manifestme`, `deployme`, `README.md`); `testme` starts the tool with one command.
- Deployed on the course **Posit Connect** server with a public URL (publisher key handed out at the
  event). Deploy early: copy one workflow from
  [`.claude/skills/connect-publish/workflows/`](.claude/skills/connect-publish/workflows/).
- A small tool that runs beats a large tool that does not.

## 6. Outputs
- **What the user sees:**
  - **Status bar (top of the page):** one box per unit in a fixed order, each with the status word: green = working (OK, WATCH, TASKS COMPLETE), yellow = MAINTENANCE, red = URGENT or CRITICAL (a CRITICAL fridge also says "move blood"). Clicking a box opens the unit.
  - **Number tiles (detail panel)** are tinted with a word (✓ ok / ⚠ watch / ▲ out), using only the thresholds already in this SPEC: temperatures green inside 1.5-5.5 °C, yellow at or beyond 1.5 / 5.5 °C, red outside 1-6 °C; Cp, Cpk, Pp, Ppk green ≥ 1.33, yellow 1.00-1.33, red < 1.00; dead-time U green ≤ 1%, yellow above; T_max green if at least the test interval; failures N yellow if any. Sigma has no colour (no threshold on its own).
  - **Display names:** the page shows MAINTENANCE as **ROUTINE MAINTENANCE**, URGENT as **URGENT MAINTENANCE** and CRITICAL as **CRITICAL FAILURE**; the API keeps the short codes.
  - **Today's attention list:** every fridge and generator with status OK / WATCH / MAINTENANCE / URGENT / CRITICAL, most urgent first, each with a recommended task. How to read it: start at the top and work down. "Now" is the last timestamp in the data (adjustable as-of time).
  - **Fridge escalation:**
    - WATCH: exactly one beyond-limit hour in the last 24 h and no run of 8 (amber on the chart, not an action).
    - MAINTENANCE: drift (a run of 8, or 2 or more beyond-limit hours, in the last 24 h; the chart shows the whole week), rising compressor duty (latest-week mean duty at least 10 percentage points above the baseline week), Ppk < 1.33, a stuck probe, a reading outside 1-6 °C earlier in the latest week (review that excursion), or an overdue service interval (coils 90 d, probe calibration 180 d, full service 365 d).
    - URGENT (check the unit now, have a backup fridge ready; no blood move): a MAINTENANCE alert is active and a raw reading in the last 24 h reaches ≥ 5.5 °C or ≤ 1.5 °C, or any alert has been active for more than 24 h without its task being completed.
    - CRITICAL (move blood now, quarantine for review): any raw reading in the last 24 h outside 1-6 °C.
    - Sudden events in the last 24 h alert immediately at MAINTENANCE: power loss (power_on = 0) and door open > 5 min (door_open = 1 on 2 or more consecutive readings).
  - **Generator alerts:** a maintenance ticket if a test fails to start, takes > 10 s, or shows low battery, low fuel or unstable output; also on an out-of-control p-chart point, an overdue weekly start test (7 d), monthly load test (30 d) or generator service (365 d), or a dead-time risk U > 1%.
  - **Equipment detail:** the I-MR chart with out-of-control points in red and the 1-6 °C spec band shaded, plus Cpk (fridges) or the p-chart (generators), days since each service type, and recommended tasks.
  - **Routine tasks checklist:** yes/no boxes per unit (fridge: coil cleaning, probe calibration, full service; generator: generator service, weekly start, monthly load); each tick logs service_type "task complete" with notes "<unit>: <service>" and today's date, counts as that service done (days-since 0) and clears its interval alert. The log downloads as a CSV.
  - **How alerts clear:**
    - Service-interval alerts are cleared by a person: a checklist "yes".
    - **Task complete (one button per alert, next to its task):** pressing it clears that one alert and adds one log row: service_type "task complete", notes "<alert id>: <task text>", with who and when. It resolves the alert if its evidence is no newer than that moment. For a service-overdue alert the row also counts as that service done, so the matching checklist box fills and greys out and its days-since resets to 0. There is no unit-level button.
    - **Tasks complete (status):** when every alert a unit had has been cleared this way and none remain, the unit shows **TASKS COMPLETE** (green) with "resolved by X at T".
    - **Re-trigger:** if the data flags again after a task was completed (a new signal hour, out-of-spec or stuck reading, door or power event, a newer failing generator test, or newer data that still shows a whole-week problem), the alert comes back, marked "re-triggered after maintenance".
    - Without "Task complete", data alerts (I-MR signal, stuck probe, Ppk < 1.33, a reading outside 1-6 °C) clear on their own when the data returns to normal (for I-MR: no signal in the last 24 h), and generator test alerts clear on the next passing test.
    - **Finished tasks stay visible:** a completed alert stays in the unit's list as a green **✓ DONE** row with who and when, and the unit's colour is recalculated from the alerts that remain (e.g. red → yellow → green). There is no Acknowledge button (old logs with acknowledge rows still load).
    - **Moving blood:** only CRITICAL (a reading outside 1-6 °C) needs a blood move: its alert "reading outside 1-6 C" is flagged `moves_blood` by the API. On that alert the Task complete button is replaced by a solid red **Move <fridge>'s blood to <backup>** button. The backup is another fridge that is green, else yellow, never an emptied one, preferring one not already holding other blood; if none is free it reads "to a backup fridge" (one brought in). Confirming logs service_type "blood moved", notes "<from> -> <to>" (or "-> backup"), and completes that alert. The fridge then shows **EMPTY: blood in <to>** and the backup **IN USE · also holding <from>'s blood**. Once every task of the emptied fridge is complete, it offers "All tasks complete: move blood back", which logs "blood returned". Every fridge box shows IN USE or EMPTY, and says "move blood" only while a move-blood alert is open.
    - **Pinned and emptied units:** a unit picked in the status bar stays first while it has open alerts or is EMPTY; an EMPTY fridge stays near the top until its blood is moved back; once a unit has nothing left to do it drops back to its place near the end.
    - **Dataset downloads:** for the selected test dataset, the page links one combined sheet (`GET /data/all_data.csv?scenario=...`; format above in section 3), built from the bundled files for scenarios 1-3 and from the generated week for scenario 4; option 5 links the template instead.
    - **Where the log lives:** the API stores nothing. The page keeps the maintenance log (task complete rows and blood moves) in memory for the current visit only (reopening the page starts fresh, one log per dataset) and sends it with each request; the log downloads as a CSV (no log upload). The bundled `maintenance_log.csv` is the starting history.
  - Generator thresholds: slow start > 10 s, low battery < 12.2 V, low fuel < 50%, and output_stable = 0, judged on the latest test of each type.
  - All thresholds and intervals are adjustable defaults.

## 7. Test datasets and codebook (rubric: demo 10, datasets 5, codebook 5)
2-3 small CSVs in `data/`, invented but shaped like real field data, each showing one behavior.
`data/README.md` is the codebook: every file, every column with units, and what it would take to
collect this data for real.
- **Datasets:** `fridge_telemetry.csv` (4 units: healthy, drifting warm, door-seal problem, stuck probe), `generator_tests.csv` (2 generators: healthy, recent failed start), `maintenance_log.csv` (service history, with one unit overdue).

## 8. Documentation and reproducibility (rubric: docs 10, public repo 5)
- `README.md` says what the tool does, for whom, every input and parameter, how to run it in one
  command, and walks through the demo on each test dataset.
- Public GitHub repo; a fresh clone runs `testme` and the tests pass with no extra steps.
- Submit on Devpost: the repo link plus a demo.

## 9. Out of scope
What we will NOT build, so nobody (human or agent) builds it by accident.
- Default: logins, databases, real customer data, a second statistic before the first one is tested.
- No real sensor hardware or live sensor pinging; no forecasting; no multi-site comparison. Build order: fridge I-MR + Cpk tested and deployed first, then the generator p-chart, then the checklist and log, then the rest.
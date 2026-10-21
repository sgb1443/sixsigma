# Plan: blood bank equipment monitor (HW team 11)

Due Fri Oct 23, 3 PM ET. Statistics first, then the tool, then the page. Test and commit each step.
Formulas and known answers are in `SPEC.md` section 4. "Degenerate" = data the math can't use normally.

## Tasks

1. **H-03, step 1: fridge statistics** (`app/qc.py`, `app/requirements.txt`, `tests/`, about 2 h)
   - `hourly_means`: average the four 15-min readings per hour, skipping blanks.
   - `imr`: center = mean, MR-bar = mean of |x_i − x_(i−1)|, sigma = MR-bar / 1.128, UCL/LCL = center ± 3 sigma.
     Limits from the baseline week (days 1-7), points from the latest week (days 8-14).
     Flags: beyond the limits; a run of 8 on one side.
   - `capability`: Cp = (6 − 1) / (6 sigma), Cpk = min(6 − mean, mean − 1) / (3 sigma), raw readings from the last 7 days.
   - `stuck_probe`: ≥ 12 identical readings in a row, or MR-bar = 0 over the last 24 h.
   - Known answer: 4.0, 4.2, 3.9, 4.1, 4.3 gives mean 4.10, MR-bar 0.225, sigma 0.1995, UCL 4.698, LCL 3.502, Cp 4.18, Cpk 3.18.
   - Degenerate: constant data gives "no variation" (not inf or a crash); n = 1 gives a clear error; missing values are skipped.
2. **H-03, step 2: generator statistics** (same files, about 1.5 h)
   - `p_chart`: p-bar = total failures / total tests, sigma_i = sqrt(p-bar(1 − p-bar) / n_i), UCL = p-bar + 3 sigma_i, LCL = max(0, p-bar − 3 sigma_i).
   - `dead_time`: lambda = failures / days in service, U = 1 − (1 − e^(−lambda T)) / (lambda T), approximation lambda T / 2, T_max ≈ 0.02 / lambda.
   - Known answers: p-bar 0.04 with n = 4 gives UCL 0.334 and LCL 0. lambda 0.001, T 30 gives U 0.014851 (approximation 0.015).
   - Degenerate: zero failures gives "no failures observed, estimate not available"; zero tests gives a clear error.
3. **H-04: API** (`app/`, about 2 h). Copy `demos/fastapi/` to `app/`.
   - Endpoints `/health`, `/fridges/{id}`, `/generators/{id}`, `/attention`, all calling `qc.py` only.
   - Escalation tiers and service intervals are plain rules in `app/rules.py`, with their own tests.
   - Done when `testme` starts it and `/fridges/FR-01` returns the same numbers as the H-03 tests.
4. **H-08, first half: public repo** at github.com/sgb1443/sixsigma, as soon as step 3 runs.
5. **H-06: deploy** (about 1 h). Workflow and manifest, then open the public URL logged out.
6. **H-05: page and demo** (`app/frontend/`, `demo/`, about 3 h). Built from `demos/reactfront/`.
   - Attention list, then I-MR chart with out-of-control points in red and the 1-6 °C band shaded.
   - Then the maintenance checklist and log, CSV download last.
   - `demo/README.md` walks through FR-01..04 and GEN-A/B.
7. **H-07: README** (about 1 h): the tool, inputs, endpoints, one-command run, live URL, limits.
8. **H-08, second half:** fresh-clone test, then submit on Devpost.

## Decisions (2026-10-20)
- Fridge I-MR uses hourly means. On raw 15-min data, healthy FR-01 had 41 false signals a week; on hourly means, 5.
- Limits come from the baseline week, and the latest week is judged against them, so a drift cannot widen its own limits.
- Stuck-probe check: 12 identical readings (3 h), or MR-bar = 0 over the last 24 h.
- Generator service interval: 365 days.
- The p-chart is the trend view; every failed start is ticketed on its own. The README states the small-n limit.
- Dead time uses the exact U, with lambda T / 2 alongside; flag at U > 1%.
- Low battery < 12.2 V and low fuel < 50%. All thresholds are adjustable defaults.
- Stack: FastAPI plus React Vite. The `.Rproj` file is unused.

## Biggest risks
1. **Scope is large for 2.5 days.** Fridge stats get deployed first; the escalation, checklist and CSV are cut first if time runs short.
2. **Autocorrelation:** Cpk on raw readings is optimistic. The README says so and shows the numbers anyway.
3. **First deploy fails late.** Deploy right after step 3 and fix it while there is still time.

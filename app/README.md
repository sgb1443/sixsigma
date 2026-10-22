# Blood bank equipment monitor: API

Main README (what it is, the statistics, assumptions): [../README.md](../README.md).

FastAPI service over `qc.py` (statistics) and `rules.py` (escalation tiers). The API stores nothing:
the page sends its maintenance log with each request. Bundled data lives in `app/data/` (copies of
`data/*.csv`; a test checks they stay identical).

## Run

```bash
bash app/testme.sh        # http://localhost:8000/docs
```

## Endpoints

- `GET /` : the dashboard page (built from `frontend/`, served from `app/static/`)
- `GET /health`
- `GET /scenarios` : the test scenarios (`key`, `label`, `description`): `mixed` (default), `healthy`, `blackout`, `random`
- `GET /fridges/{unit_id}?as_of=` : `fridge_summary` plus `status`, `alerts`, `days_since`
- `GET /generators/{gen_id}?as_of=` : `generator_summary` plus `status`, `alerts`, `days_since`
- `GET /attention?as_of=` : all 6 units, most urgent first (bundled log); each unit has `days_since`, with the log entries counted
- `POST /attention` : body `{"log": [entries], "as_of": optional, "scenario": optional, "seed": optional}`; entries have the columns of
  `maintenance_log.csv`, and `service_type` may be `task complete`, `blood moved` or `blood returned` (notes `FROM -> TO`; ignored by the rules), or the legacy `acknowledge`
  (still read for old logs; the page no longer has the button) or `task complete` (technician = who; see below). The old unit-level `maintenance complete` row is still read.
- `GET /log` : the scenario's bundled maintenance log
- `GET /data/{fridge_telemetry|generator_tests|maintenance_log}.csv?scenario=&seed=` : download that scenario's input CSV (bundled file byte for byte; scenario 4 as generated)
- Unknown id: 404. Bad log entry: 422. Bad `as_of`: 400.

## Scenarios

Every data endpoint (`/fridges/{id}`, `/generators/{id}`, `/attention` GET and POST, `/log`) takes an
optional `scenario` query parameter (POST also accepts it in the body); default `mixed`. Keys:
`mixed`, `healthy`, `blackout` (CSV files in `app/data/`, `app/data/all_healthy/`,
`app/data/blackout_day/`, loaded once) and `random`, which needs an integer `seed`
(`?scenario=random&seed=2026`; body field `seed`): the same seed always gives the same generated
data (`app/simulate.py`, cached for the last 16 seeds), and `/attention` then also returns an
`answer_key` (what was planted per unit). Unknown scenario: 404. `random` without a valid seed: 422.
What each scenario plants: [../data/README.md](../data/README.md#scenarios).

## Task complete

A log row with `service_type` `task complete` and `notes` = `"<alert id>: <task text>"` (the per-alert
button, sent after a confirm), for example `FR-02:coil cleaning overdue: Clean the condenser coils and log it.`
It resolves that alert if its evidence is no newer than the row; if the data flags again later, the
alert returns marked "re-triggered after a task was completed". On an overdue-service alert it also
counts as that service done on that date (the alert clears and `days_since` becomes 0). A routine-task
tick is a `task complete` row with `notes` `"<unit id>: <service>"`, for example `FR-03: coil cleaning`.
A unit with something resolved and no active alert has status `TASKS COMPLETE`.

## Scripts

`testme.sh` (run locally), `manifestme.sh` (write `manifest.json`), `deployme.sh` (publish to Posit
Connect). Entry point is `main:app`.

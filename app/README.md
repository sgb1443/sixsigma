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
- `GET /fridges/{unit_id}?as_of=` : `fridge_summary` plus `status`, `alerts`, `days_since`
- `GET /generators/{gen_id}?as_of=` : `generator_summary` plus `status`, `alerts`, `days_since`
- `GET /attention?as_of=` : all 6 units, most urgent first (bundled log)
- `POST /attention` : body `{"log": [entries], "as_of": optional}`; entries have the columns of
  `maintenance_log.csv`, and `service_type` may be `acknowledge` (alert id in `notes`) or
  `maintenance complete` (technician = who)
- `GET /log` : the bundled maintenance log
- Unknown id: 404. Bad log entry: 422. Bad `as_of`: 400.

## Maintenance complete

A log row with `service_type` `maintenance complete`. The page asks for a confirm before sending it.
It resolves every alert of that unit whose evidence is no newer than the row; if the data flags
again later, the alert returns marked "re-triggered after maintenance".

## Scripts

`testme.sh` (run locally), `manifestme.sh` (write `manifest.json`), `deployme.sh` (publish to Posit
Connect). Entry point is `main:app`.

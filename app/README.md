# Blood bank equipment monitor: API

FastAPI service over `qc.py` (statistics) and `rules.py` (escalation tiers). The API stores nothing:
the page sends its maintenance log with each request. Bundled data lives in `app/data/` (copies of
`data/*.csv`; a test checks they stay identical).

## Run

```bash
bash app/testme.sh        # http://localhost:8000/docs
```

## Endpoints

- `GET /health`, `GET /`
- `GET /fridges/{unit_id}?as_of=` : `fridge_summary` plus `status`, `alerts`, `days_since`
- `GET /generators/{gen_id}?as_of=` : `generator_summary` plus `status`, `alerts`, `days_since`
- `GET /attention?as_of=` : all 6 units, most urgent first (bundled log)
- `POST /attention` : body `{"log": [entries], "as_of": optional}`; entries have the columns of
  `maintenance_log.csv`, and `service_type` may be `acknowledge` with the alert id in `notes`
- `GET /log` : the bundled maintenance log
- Unknown id: 404. Bad log entry: 422.

## Scripts

`testme.sh` (run locally), `manifestme.sh` (write `manifest.json`), `deployme.sh` (publish to Posit
Connect). Entry point is `main:app`.

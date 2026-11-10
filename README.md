# Thermal Anomaly Detection API

A small FastAPI + SQLite service for equipment, thermal inspections, alert generation, and operator acknowledgements.

## Run

From this folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000/docs` for the interactive API page. The SQLite database defaults to `thermal_anomaly.db` in this project folder, independent of the launch directory. Set `DATABASE_URL` to use another database.

## Load the supplied example data

Start the API, then in another terminal from this folder run:

```bash
python scripts/load_sample_data.py
```

The loader posts each asset and inspection through the API and prints the created inspection results and open alerts. Existing assets are skipped on reruns; repeated inspections with identical content are safely returned as duplicates.

## Endpoints

- `POST /assets` and `GET /assets`
- `POST /inspections` creates an inspection and automatically evaluates its asset's thresholds
- `GET /inspections?asset_id=BLR-01` lists records newest first; `GET /assets/{asset_id}/inspections` is the asset-specific form
- `GET /alerts` lists open alerts by default; `?status=acknowledged` lists acknowledged alerts
- `POST /alerts/{id}/acknowledge` with `{"acknowledged_by":"operator name"}` changes state and writes an audit record
- `GET /health`

## Behavior and design

An inspection at or above the critical limit creates a critical alert. Otherwise, one at or above the warning limit creates a warning alert. Lower readings do not create alerts. Critical is evaluated first, so a reading cannot be downgraded to warning.

The database uniquely constrains `(asset_id, timestamp)`. A retry with the same asset, timestamp, temperatures, and image reference returns the original inspection and alert outcome without inserting anything twice. Reusing that key with different contents returns HTTP 409. Timestamps are normalized to UTC before storage and comparisons, avoiding timezone-offset variants of the same instant being treated as separate readings. SQLite returns stored timestamps without timezone metadata, so API responses label those stored values as UTC.

## Tests

```bash
pytest -q
```

## AI assistance disclosure

AI assistance (ChatGPT) was used to review the existing backend against the brief, implement missing API behavior and validation, add automated tests and sample-data loading, and draft this explanation. The developer should review and be prepared to explain every design choice.

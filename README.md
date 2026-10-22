# Security deposit claims workspace

Look up claims, inspect cited PDF evidence, and compare optional research predictions
with recorded outcomes. Python/FastAPI backend; React/TypeScript frontend.
All recommendations require review. A decline prediction is not a supported denial.

## Run with Docker

```sh
docker compose up --build -d
```

Open [localhost:8080](http://localhost:8080) and look up `DEMO-001, DEMO-002`.
These are synthetic records with no historical decisions or evidence. No Google
account is needed. On this configured workspace, recommendations and evaluation
metrics use the private artifacts mounted from `cache/review/`. A fresh clone
needs its own model files and hash settings from `.env.example`; see the
[model setup](documentation/reference.md#model-setup).

Place your own CSV/PDFs in the ignored `data/` folder. To look up your CSV:

```sh
DOCKER_CLAIMS_CSV_PATH=/inputs/claims.csv docker compose up -d
```

Document review accepts manifests whose PDF paths are inside the container,
e.g. `/inputs/ledger.pdf`. Extraction results persist in a named Docker volume.
`docker compose down` stops the app and keeps that volume.

## Run locally

Requires Python 3.13, Node 24, and Tesseract with English data for OCR.

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt -r requirements-prediction.txt
yarn install --frozen-lockfile
cp .env.example .env
.venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

In another terminal, run `npm run dev` and open [localhost:5173](http://localhost:5173).
If `.env` already exists, edit it instead of copying over it.

```sh
npm test          # frontend and Python tests
npm run check    # lint, TypeScript/production build, and all tests
```

## Use Google Drive

Live lookup needs access to the claims spreadsheet and evidence folder. Another
person needs their own OAuth authorization and permission to those sources.
The frontend alone cannot grant access. CSV mode and local document review work
without Drive.

Set the IDs in `drive-sources.json`, obtain a Desktop OAuth client at
`credentials/drive-oauth-client.json`, and authorize once on the host:

```sh
.venv/bin/python -c "from pathlib import Path; from backend.drive import connect_drive; connect_drive(Path('credentials/drive-oauth-client.json'), Path('secrets/drive-token.json')).close()"
```

Leave `CLAIMS_CSV_PATH` empty for local Drive mode. For Docker:

```sh
docker compose -f compose.yaml -f compose.drive.yaml up --build -d
```

Keep credentials private. The app is intended for a trusted local machine;
Docker publishes it only on localhost. The API can read local manifest paths.

## Details

[Configuration and limitations](documentation/reference.md) covers document review,
caching, recovery, private model setup, and research boundaries.

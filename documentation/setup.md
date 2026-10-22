# Setup guide

See the [README](../README.md) for quick startup.

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

## Use downloaded claims and PDFs

You can review downloaded Drive files without Google credentials or an internet
connection after the Docker images have been built. Export the claims tab from
Google Sheets using **File → Download → Comma Separated Values (.csv)**. Download
the evidence PDFs or their containing folder from Drive. Unzip folder downloads
before adding them; ZIP files and Google shortcut files are not read by the app.

Place the files in the repository's ignored `data/` folder:

```text
data/
  claims.csv
  evidence/
    1/
      ledger.pdf
      lease.pdf
    2/
      move-out/
        itemization.pdf
```

Keep the CSV's original headers, including `Tracking Number` (see
`examples/claims.csv`). Each immediate folder under `evidence/` must match a
tracking number exactly. Nested PDF folders are supported; other file types and
symlinks are skipped. The CSV supplies claim details; PDFs alone do not create
claim records.

From the repository root, with Docker Desktop running:

```sh
docker compose -f compose.yaml -f compose.local.yaml up --build -d
```

The default startup also detects these files when Drive is unavailable. Use the
local override to explicitly select local files instead of Drive. It selects
`/inputs/claims.csv` and `/inputs/evidence`, which map to the host's `data/`
directory. It needs no `.env` edits or Google authorization. Open
[localhost:8080](http://localhost:8080), look up a number from the CSV, and open
its PDFs in **Evidence → Documents**. Lookup rereads the CSV and folder contents,
so replacing files requires a fresh lookup, not a container rebuild. These are
downloaded snapshots; Drive changes are reflected only after you download them
again. A claim without a matching folder shows missing evidence.

To extract facts and inspect citations, use **Document review** with paths under
`/inputs/`, for example:

```json
{
  "claims": [{
    "claim_id": "1",
    "documents": [
      {"document_id": "ledger", "kind": "ledger", "path": "/inputs/evidence/1/ledger.pdf"},
      {"document_id": "lease", "kind": "lease", "path": "/inputs/evidence/1/lease.pdf"}
    ]
  }],
  "retry": false
}
```

Opening a PDF from lookup does not automatically run extraction. Optional
research predictions still need the [model setup](reference.md#model-setup).

For the Python backend running directly on your computer, set these in `.env`
and restart it:

```dotenv
CLAIMS_CSV_PATH=data/claims.csv
CLAIMS_EVIDENCE_ROOT=data/evidence
```

Document review then uses host paths such as `data/evidence/1/ledger.pdf`.
For a different Docker layout, use `DOCKER_CLAIMS_CSV_PATH` and
`DOCKER_CLAIMS_EVIDENCE_ROOT` with paths under `/inputs/` in `.env`, and start with
just `docker compose up --build -d` (the local override fixes the standard paths).

Keep downloaded claims in `data/` so they stay out of Git. To switch back to live
Drive, use the startup command in the next section; the local files remain saved.

## Use Google Drive

Live lookup needs access to the claims spreadsheet and evidence folder. Another
person needs their own OAuth authorization and permission to those sources.
The frontend alone cannot grant access. CSV mode and local document review work
without Drive.

### 1. Confirm access and configure the sources

Sign in to Google Drive with the account you will authorize below. Open the
claims spreadsheet, the evidence root folder, and a claim's PDFs to confirm you
can view and download them. Ask the source owner for access if needed.

Edit `drive-sources.json` in the repository root:

```json
{
  "claims_file_id": "YOUR_CLAIMS_SPREADSHEET_ID",
  "evidence_root_folder_id": "YOUR_EVIDENCE_ROOT_FOLDER_ID"
}
```

For a spreadsheet URL such as `https://docs.google.com/spreadsheets/d/FILE_ID/edit`,
copy `FILE_ID`. For a folder URL such as
`https://drive.google.com/drive/folders/FOLDER_ID`, copy `FOLDER_ID`. Keep the
existing IDs if you are using the same shared sources.

The claims source must be a Google spreadsheet or CSV with a `Tracking Number`
column; see `examples/claims.csv` for the other expected columns. For a Google
spreadsheet, put the claims table on the first tab (the CSV export reads that
tab; see Google's [export formats](https://developers.google.com/workspace/drive/api/guides/ref-export-formats)).
Evidence belongs in immediate subfolders of the evidence root, with each
subfolder named for its claim's tracking number. A claim can load without a
matching folder, but its evidence will be marked missing.

### 2. Create a Google Desktop OAuth client

If your project administrator already supplied a Desktop OAuth client JSON,
save it at the path below and proceed to step 3. Each person still authorizes
their own Google account; do not copy another person's token.

Otherwise, follow these steps in the [Google Cloud console](https://console.cloud.google.com/):

1. Create or select a project. In **APIs & Services → Library**, find and enable
   **Google Drive API**.
2. Open **Google Auth platform → Branding**. Click **Get started** if prompted,
   and provide an app name (for example, `Claims local review`), support email,
   and contact email.
3. Under **Audience**, choose **Internal** for users in your Workspace
   organization, if available. For a personal Google account or outside users,
   choose **External**, leave it in **Testing**, and add the signing-in account
   under **Test users**.
4. Under **Data Access → Add or Remove Scopes**, add
   `https://www.googleapis.com/auth/drive.readonly` and save. This is the scope
   requested by this app; it allows reading and downloading Drive files that
   your account can access.
5. Under **Clients → Create client**, select **Desktop app**, give it a name,
   and create it. Download the client JSON.

These console steps follow Google's [Drive setup guide](https://developers.google.com/workspace/drive/api/quickstart/python)
and [consent-screen guide](https://developers.google.com/workspace/guides/configure-oauth-consent).

From the repository root, create the private directories:

```sh
mkdir -p credentials secrets
chmod 700 credentials secrets
```

Move the downloaded JSON to `credentials/drive-oauth-client.json`, then run:

```sh
chmod 600 credentials/drive-oauth-client.json
```

### 3. Authorize your account on the host

Even when running the app through Docker, this one-time browser sign-in uses
Python on your computer. Install Python 3.13 if needed, then run these commands
from the repository root. Reuse `.venv` if it already exists.

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements-backend.txt
```

Authorize with:

```sh
.venv/bin/python -c "from pathlib import Path; from backend.drive import connect_drive; connect_drive(Path('credentials/drive-oauth-client.json'), Path('secrets/drive-token.json')).close()"
```

In the browser, select the account with source access and grant the requested
read-only Drive access. Wait for the command to finish. It creates
`secrets/drive-token.json`; subsequent requests reuse or refresh that token.
Docker cannot open this interactive sign-in flow for you.

### 4. Start Docker with the Drive override

With Docker Desktop running, use both Compose files every time you start Drive
mode:

```sh
docker compose -f compose.yaml -f compose.drive.yaml up --build -d
docker compose -f compose.yaml -f compose.drive.yaml ps
```

Wait for the backend to show `healthy`, then open or refresh
[localhost:8080](http://localhost:8080). Enter a tracking number from your actual
spreadsheet. The `DEMO-001, DEMO-002` records belong to sample CSV mode.
Successfully loading a real claim confirms source access; container health
alone does not check Google permissions.

The override mounts your credentials, token directory, and source configuration,
and clears `CLAIMS_CSV_PATH` to select Drive. You do not need to replace your
existing `.env`. If running the backend directly instead, set `CLAIMS_CSV_PATH=`
in `.env` and restart it.

Drive access supplies claims and evidence. Optional research predictions still
need the separate [model setup](reference.md#model-setup).

### Troubleshooting and reauthorization

- **Google blocks sign-in:** Confirm the account is an allowed test user or
  belongs to the Internal app's organization. If a Workspace administrator
  blocks the app or Drive scope, ask them to approve access.
- **Source unavailable:** Check that Drive API is enabled in the OAuth client's
  project, the IDs are correct, and the authorized account can open and download
  the sources. Check backend details with
  `docker compose -f compose.yaml -f compose.drive.yaml logs --tail=100 backend`.
- **Authorization expired or `invalid_grant`:** Reauthorize below. For External
  apps in Testing, Google issues refresh tokens that
  [expire after seven days](https://developers.google.com/identity/protocols/oauth2#expiration)
  when requesting this Drive scope.
- **Claim not found or evidence missing:** Use a tracking number from the live
  spreadsheet and check the matching evidence subfolder name and permissions.
- **Still seeing demo claims:** Restart with both Compose files as shown above.

To change Google accounts or replace an invalid token, stop the app and move the
old token aside before repeating the authorization command in step 3:

```sh
docker compose -f compose.yaml -f compose.drive.yaml down
mv secrets/drive-token.json "secrets/drive-token.backup-$(date +%Y%m%d-%H%M%S).json"
```

If the token file is already missing, skip the `mv` command. After authorizing,
start Docker again using step 4. Token backups contain credentials too.

To return to sample claims:

```sh
docker compose -f compose.yaml -f compose.drive.yaml down
DOCKER_CLAIMS_CSV_PATH=/app/examples/claims.csv docker compose up --build -d
```

Keep `credentials/` and `secrets/` private; both are ignored by Git. The app is
intended for a trusted local machine; Docker publishes it only on localhost.
The API can read local manifest paths.

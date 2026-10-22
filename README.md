# Security deposit claims workspace

Look up claims, review PDF evidence, and compare research predictions with
recorded outcomes. Predictions require review; they do not establish coverage.

## Start

With Docker Desktop running, from the repository root:

```sh
docker compose up --build -d
```

Open [localhost:8080](http://localhost:8080). The app uses authorized Google Drive
when available, otherwise local files. If neither is ready, it shows setup
instructions.

For Google Drive, complete the [one-time setup](documentation/setup.md#use-google-drive), then start with:

```sh
docker compose -f compose.yaml -f compose.drive.yaml up --build -d
```

## Local files

Unzip downloaded PDFs and place files in the ignored `data/` folder:

```text
data/
  claims.csv
  evidence/
    1/
      lease.pdf
      ledger.pdf
```

Keep the CSV headers, including `Tracking Number`. Name each evidence folder for
its tracking number, then click **Check connection**. No restart is needed.

## Development

[Local setup](documentation/setup.md#run-locally) · [Configuration and model setup](documentation/reference.md)

```sh
npm run check        # lint, build, and tests
docker compose down  # stop; cached extraction stays saved
```

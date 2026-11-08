# PennyTracker

A local-only personal finance ledger. The first milestone provides Django/DRF with SQLite and a React/TypeScript frontend. The browser reads the ledger from the local API; add and edit records through Django admin.

## Recommended: Docker Compose

Install Docker Desktop, then from the repository root run:

```sh
docker compose -f docker-compose.yaml up
```

Open the Vite URL at `http://localhost:5173`. Django admin is at `http://localhost:8000/admin/`; create its first login in another terminal:

```sh
docker compose -f docker-compose.yaml exec backend python manage.py createsuperuser
```

Backend and frontend source directories are bind-mounted for reloads. Frontend packages are installed from the lockfile at startup, and SQLite data persists in the `pennytracker-data` named volume. Stop the services with `Ctrl-C`; use `docker compose -f docker-compose.yaml down` to stop and remove the containers while keeping the database volume.

The published ports are bound to localhost. This setup is for local development, not production deployment. If Python dependencies change, rebuild with `docker compose -f docker-compose.yaml up --build`; frontend dependencies are refreshed from the lockfile at each startup.

## Import a CSV

Create an active account in Django admin, open PennyTracker, and choose **Import CSV**. Select the destination account and a supported CSV file (maximum 20 MiB), preview it, then confirm. The original filename is audit metadata only; the selected account and CSV contents determine the import. The first importer accepts posted SoFi checking CSVs, and exact file re-imports to the same account are blocked.

## Run without Docker

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python backend/manage.py migrate
.venv/bin/python backend/manage.py runserver 127.0.0.1:8000
```

In another terminal, run `cd frontend && npm install && npm run dev`, then open the Vite URL shown.

## Checks

```sh
.venv/bin/python backend/manage.py test finance
cd frontend && npm run build
```

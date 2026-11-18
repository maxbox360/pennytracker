# Development and testing

For the recommended Docker-based first run, see [Setup](SETUP.md). The following checks are intended for contributors and use the repository's existing Python and npm environments.

## Standard checks

From the repository root:

```sh
.venv/bin/python backend/manage.py test finance
cd frontend
npm run build
```

To check that model changes have migrations, run from the repository root:

```sh
.venv/bin/python backend/manage.py makemigrations --check --dry-run
```

## Optional Review browser regression

This regression requires Node.js 22 or later and Chrome. It modifies synthetic transactions, so use a **new disposable database and a separate temporary Chrome profile**. Do not point it at the database used for normal PennyTracker use. The fixture script refuses to seed a nonempty ledger.

From the repository root, create and migrate a temporary SQLite database, then seed the browser fixtures:

```sh
export DJANGO_DB_PATH="$(mktemp -d)/pennytracker.sqlite3"
.venv/bin/python backend/manage.py migrate
PENNYTRACKER_BROWSER_FIXTURES=1 .venv/bin/python backend/manage.py shell < frontend/tests/review-browser-fixtures.py
DJANGO_CSRF_TRUSTED_ORIGINS=http://127.0.0.1:15173 .venv/bin/python backend/manage.py runserver 127.0.0.1:18080
```

In another terminal, from the repository root, start the frontend with its API proxy pointed to that disposable backend:

```sh
cd frontend
VITE_API_PROXY_TARGET=http://127.0.0.1:18080 npm run dev -- --host 127.0.0.1 --port 15173 --strictPort
```

Open that frontend in Chrome with `--remote-debugging-port=19222` and a separate temporary profile. Then, from the repository root, run:

```sh
npm --prefix frontend run test:review-browser
```

The test expects the fixture database to be freshly seeded. Its setup and fixture are specifically for browser regression, not for creating or populating a normal user installation.

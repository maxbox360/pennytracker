# Installation and first-run setup

This guide takes you from a fresh checkout to a working, empty PennyTracker installation. An empty ledger after launch is expected; PennyTracker does not ship with your financial history.

## Requirements

### To run PennyTracker normally

- Git, to clone the repository.
- Docker with Docker Compose v2 (`docker compose`).

The Compose setup builds and runs both application services. You do not need to install Python or Node.js for this route.

### Only for native development

The container images use Python 3.12 and Node.js 22. For a native setup, install those runtimes, npm, and Python's `venv` support. The backend Python dependencies are listed in `requirements.txt`; the frontend dependencies and lockfile are in `frontend/`.

## Start with Docker Compose

Clone the repository and start the services:

```sh
git clone https://github.com/maxbox360/pennytracker.git
cd pennytracker
docker compose -f docker-compose.yaml up
```

Compose builds the backend and frontend images. The backend runs Django migrations at startup and then starts Django; the frontend starts Vite. Open PennyTracker at <http://localhost:5173>. Django Admin is at <http://localhost:8000/admin/>.

Keep that terminal open while using the app. Press `Ctrl-C` to stop the services. The SQLite database is stored in the Compose-managed `pennytracker-data` volume and remains when containers are stopped or recreated. Do not remove that volume if you want to keep its data.

## Create the first admin user and account

A fresh database has no admin login or Accounts. In a second terminal, from the repository root, create a Django Admin login:

```sh
docker compose -f docker-compose.yaml exec backend python manage.py createsuperuser
```

Follow the prompts, then sign in at <http://localhost:8000/admin/>. Under **Finance → Accounts**, choose **Add account**. The account fields are:

- **Name**: a local label, such as `Example Checking`.
- **Institution**: optional text label; it does not connect to that institution.
- **Account type**: Checking, Savings, Credit card, Loan, Investment, or Other.
- **Is savings destination**: account metadata only. It does not automatically make transfers Savings activity; Savings effects are explicitly assigned to individual transactions.
- **Is active**: active accounts are available in the CSV import selector.

The web app does not currently have an Accounts management screen, so use Django Admin to create or edit Accounts.

## Create initial subcategories

The database does not include default Subcategories. In PennyTracker, open **Categories** and create the subcategories you want to use. For budget subcategories, choose a group—**Needs**, **Wants**, or **Savings**—and enter one or more names. For example, create `Groceries` under Needs and `Fast Food` under Wants. These names are examples, not built-in categories.

You can add categories later, including while resolving a transaction in Review. However, create the subcategories you expect to need before importing if you want to categorize transactions immediately.

## Confirm the first launch

- PennyTracker loads at <http://localhost:5173>.
- Sign-in to Django Admin works at <http://localhost:8000/admin/>.
- An Account created in Admin is available in **Transactions → Import CSV**.
- The **Categories** and **Import CSV** workflows open in PennyTracker.

**Seeing no transactions or reports is normal.** A new installation contains no personal ledger data. To populate it, export a CSV in a currently supported format from your financial institution and import it yourself; you do not type transactions one by one.

## Optional: run natively for development

From the repository root, create a Python environment, install backend requirements, and migrate the database:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python backend/manage.py migrate
```

In one terminal, start Django:

```sh
.venv/bin/python backend/manage.py runserver 127.0.0.1:8000
```

In another terminal, install the locked frontend dependencies and start Vite:

```sh
cd frontend
npm ci
npm run dev -- --host 127.0.0.1
```

The frontend is at <http://localhost:5173>; Django Admin is at <http://localhost:8000/admin/>. Create an admin login in another terminal from the repository root with `.venv/bin/python backend/manage.py createsuperuser`, then create an Account as described above. The native database is `backend/db.sqlite3` by default and is separate from the Docker-managed database.

## Troubleshooting

- **The ledger is empty:** This is the expected fresh-install state. Create an Account, then import a supported CSV.
- **No account appears during import:** Create and save an Account in Django Admin, and ensure **Is active** is enabled.
- **A page does not load:** Check the Compose terminal or run `docker compose -f docker-compose.yaml ps`; the frontend uses port `5173` and the backend/Admin uses port `8000`.
- **The backend reports a database or migration error:** Check its startup output or run `docker compose -f docker-compose.yaml logs backend`. Compose normally runs migrations automatically when the backend starts.
- **Your CSV cannot be previewed:** Only the supported SoFi checking CSV structure is recognized today. See the [User Guide](USER_GUIDE.md) for its requirements.

## Development fixtures are not user data

`frontend/tests/review-browser-fixtures.py` is an opt-in fixture for a disposable database used only by the optional Review browser regression. It deliberately refuses to seed a nonempty ledger. Do not use it to create a normal PennyTracker account or populate a personal database. See [Development and testing](DEVELOPMENT.md).

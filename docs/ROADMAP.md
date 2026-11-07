# PennyTracker roadmap

Implement one milestone at a time; keep the application runnable and verify each milestone before moving on.

## Milestone 1.5 — Local Docker Compose development

- Recommend `docker compose -f docker-compose.yaml up` as the local development workflow.
- Use only `backend` (Django/DRF) and `frontend` (React/Vite) application services with SQLite.
- Bind source directories for reloads, keep SQLite data persistent, and proxy browser `/api` requests through Vite to Django over Compose networking.
- Bind published ports to localhost. Do not add production stacks or infrastructure services.

## Milestone 1 — Foundation and initial ledger

- Set up Django/DRF with SQLite and React/TypeScript/Vite for local development.
- Add accounts, editable subcategories under stable Needs/Wants/Savings bucket values, and a basic transaction ledger.
- Keep transaction classification (`UNKNOWN`, `EXPENSE`, `INCOME`, `REFUND`, `TRANSFER`, `EXCLUDED`) independent from any later review/categorization state.
- Snapshot the budget bucket on categorized transactions so later subcategory moves do not rewrite historical reporting.
- Provide Django admin and a simple transaction-list screen backed by the local API.
- Do not add importing, review workflows, monthly calculations, debts, or recurring expenses in this milestone.

## Milestone 2 — CSV import

- Support one real user-provided CSV format and record each upload as an Import Batch.
- Take account identity from the selected account/import context, dates from CSV contents, and preserve the original filename only as metadata.
- Detect and automatically reject only high-confidence duplicates; send uncertain candidates to review.

## Milestone 3 — Categorization and review

- Add transparent editable categorization rules, merchant cleanup, review queue, and practical bulk actions.
- Make refunds and transfer candidates reviewable; preserve raw descriptions and imported amounts.

## Milestone 4 — Accounting and monthly reporting

- Implement and test income, Needs/Wants expenses, linked refunds, credit-card payments, internal transfers, qualifying savings transfers, and exclusions.
- Use posted date when available, otherwise transaction date. Count savings transfers once on the source-side posted date when available.
- Calculate Remaining / Unallocated as income minus Needs, Wants, and actual qualifying Savings.
- Snapshot configurable monthly allocation targets and applicable subcategory budgets when each month becomes relevant; no rollover or pre-created future months.
- Build the monthly dashboard around target-versus-actual bars and auditable drill-downs.

## Milestone 5 — Historical reporting and export

- Add useful yearly and month-to-month reporting after monthly calculations are established.
- Add transaction and reporting-data export; consider historical spreadsheet migration separately.

## Later, only when useful

- Debt records and balance snapshots, including optional extra-paydown goals.
- Recurring bills and subscriptions.
- Additional CSV formats, import profiles, statement parsing, and automatic bill matching.
- Backup/restore.

Keep the initial product local-only. Docker Compose is for local development only; do not introduce cloud services, telemetry, bank syncing, or production infrastructure.
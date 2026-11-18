# PennyTracker

PennyTracker is a local personal-finance application for importing transaction CSVs, reviewing and categorizing activity, teaching transparent rules, and understanding spending over time.

> **You do not enter every transaction manually.** The intended workflow is:
> **CSV export → Import → Review → Rules → Reports**

PennyTracker currently imports CSV files; it does not connect to bank accounts or use direct banking APIs. You export a supported file yourself and import it into a PennyTracker account.

Personal-finance spreadsheets can become increasingly manual and difficult to keep consistent. PennyTracker provides a local, auditable workflow for importing transaction history, categorizing it consistently, and producing historical reports without requiring direct bank access.

## Local-first by design

Financial records are stored in PennyTracker's local SQLite database. Docker Compose keeps that database in a local Docker-managed volume. PennyTracker does not need your bank username or password, and it does not include your financial data. A fresh installation starts with an empty ledger until you create accounts and import transactions. This is a local application, not a hosted service; the local setup is not a security guarantee for a machine that is shared or exposed to a network.

## What it supports

- CSV import preview and confirmation, with exact-file checks and review of overlapping or possible-duplicate rows.
- A transaction ledger and Review workflow for resolving classifications, categories, and duplicate questions.
- Editable Needs, Wants, Savings, and Other Inflows subcategories.
- Explicit, deterministic description-matching rules.
- All Time, calendar-year, calendar-month, YTD, 7-day, 30-day, and custom-range reports with transaction drilldowns.
- Configurable Needs/Wants/Savings allocation targets and Light, Dark, and System themes.

PennyTracker's current budgeting workflow centers on **Needs**, **Wants**, and actual **Savings** activity. Allocation targets are configurable; this is not intended as a universal budgeting framework.

The built-in CSV parser currently supports one SoFi checking export format, not arbitrary bank or card CSVs. See the [User Guide](docs/USER_GUIDE.md) for the format requirements and current import behavior.

## Requirements

For normal use, install **Git** and **Docker Compose v2** (included with Docker Desktop, or available as the Docker Compose plugin with Docker Engine). Python and Node.js are only needed if you choose to run or develop the services natively.

## New here?

1. Follow [Installation and first-run setup](docs/SETUP.md).
2. Read the [User Guide](docs/USER_GUIDE.md) for the import, Review, Rules, and Reports workflow.

Development checks and the optional synthetic browser regression are documented in [Development and testing](docs/DEVELOPMENT.md).

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

- Support the observed SoFi checking CSV format with a small explicit parser and record successful imports as Import Batches.
- Accept arbitrary date ranges in one file; preview against an explicitly selected active account, derive dates from CSV rows, and preserve the original filename only as metadata.
- Enforce a 20 MiB limit, strict ISO dates, Decimal amounts, and Posted-only rows. Any malformed or unsupported row blocks the whole import; ambiguous but valid classifications remain `UNKNOWN`.
- Preserve generic transaction provenance and batch earliest/latest transaction dates for audit. Ignore source balance. ImportBatch boundaries never determine reporting periods.
- Keep the SoFi parser-specific assumptions in the concrete parser; keep normalized data and the rest of the app institution-independent.
- Block exact file re-import per account by SHA-256. Overlapping non-identical imports remain a known limitation for the future duplicate/review workflow; do not silently deduplicate similar rows.
- Test with synthetic sanitized fixtures only; exclude root-level CSV exports from Git and Docker build contexts.

## Milestone 3 — Overlap protection, ledger review, and manual classification (complete)

- Extend ImportBatch with already-represented and possible-duplicate counts; preview and confirm overlapping imports with account-scoped, multiplicity-aware matching.
- Fingerprint the current format by transaction date, posted date when available, signed Decimal amount, source type, and normalized raw description (trim/collapse whitespace/case-fold); do not use filename, merchant, batch, or source row. Call matches `already represented`, not certain duplicates.
- Preserve candidate transaction links for possible duplicates and show comparison context in Review. Import possible duplicates into Review; never silently drop excess identical occurrences.
- Add explicit duplicate states (unresolved, distinct, confirmed duplicate), separate from classification and review status. Keep confirmed duplicates stored and exclude them from future totals.
- Add `PENDING`, `REVIEWED`, and `NOT_REQUIRED` review status, derived attention reasons, deterministic completion rules, and validation that returns edited transactions to PENDING when required.
- Support manual classification, eligible subcategory assignment with bucket snapshots, clean merchant and notes edits, Review filters, and atomic bulk classification/category/mark-reviewed actions.
- Keep imported source fields immutable in normal API edits and read-only in Django admin. Use `apiFetch` for every frontend mutation.
- Do not add automatic categorization rules, reporting, refund linking, transfer pairing, savings calculations, debt, recurring bills, or subscriptions.
- Verified with multiplicity, review-rule, atomic bulk-action, source-immutability, and duplicate-resolution tests; frontend build and Docker Compose API/proxy/CSRF smoke checks pass.

## Milestone 3.5 — Ledger UX, Categories, and themes (complete)

- Refine Transactions and Review into denser ledger tables with grouped page actions, compact status treatments, and a compact transaction editor; keep duplicate controls limited to unresolved possible duplicates.
- Add Categories management for creating, renaming, moving, archiving, and reactivating Subcategories. Block exact case-insensitive duplicate names, including archived-name collisions that should be resolved by reactivation.
- Keep archived categories available for history but unavailable for new assignments. Category bucket moves affect future assignments only; preserve existing Transaction bucket snapshots.
- Add atomic Review create-and-assign so category creation, eligibility validation, assignment, and bucket snapshot roll back together on failure.
- Add persisted Light, Dark, and System themes using CSS custom properties and synchronous startup initialization; keep the theme preference in browser localStorage.
- Keep all mutations on the shared CSRF-aware `apiFetch` client. Do not add categorization rules or reporting.
- Verified with 49 Django tests, frontend TypeScript/production build, no pending migrations, and the running Docker Compose frontend/API smoke check.

## Milestone 3.6 — Ledger query usability and transfer clarifications (complete)

- Replace green-cast Dark/System-dark surfaces with neutral charcoal/slate tokens while keeping green for meaningful accents; leave Light tokens unchanged.
- Add consistent API-backed sorting and filtering on Transactions and Review for date, description/merchant, account, source type, classification, subcategory/bucket, and review status/reason.
- Paginate the transaction API and ledger UI so sorting/filtering does not require downloading the whole transaction history.
- Preserve the rule that owned-account movement is `TRANSFER`, not Income or ordinary spending. Keep generic `DEPOSIT` source rows `UNKNOWN`; a Transfer can be manually reviewed without a Subcategory.
- Document future direction-derived Savings contributions/withdrawals and two-sided transfer pairing without implementing pairing, savings calculations, or reports.
- Verified with 54 Django tests, frontend TypeScript/production build, no pending migrations, and the running Docker Compose paginated API/frontend smoke checks.

## Milestone 4 — Accounting and monthly reporting

- Implement and test income, Needs/Wants expenses, linked refunds, credit-card payments, internal transfers, qualifying savings transfers, and exclusions.
- Use posted date when available, otherwise transaction date. Count savings transfers once on the source-side posted date when available.
- Calculate Remaining / Unallocated as income minus Needs, Wants, and actual qualifying Savings.
- Snapshot configurable monthly allocation targets and applicable subcategory budgets when each month becomes relevant; no rollover or pre-created future months.
- Build the monthly dashboard around target-versus-actual bars and auditable drill-downs.

## Milestone 5 — Historical reporting and export

- Add All Time and calendar-year views, year-over-year comparisons, subcategory breakdowns, and spending/savings trends across months and years.
- Keep all reporting periods independent of imports and drill from every aggregate to the contributing transactions using historical bucket snapshots.
- Leave custom date ranges straightforward to add later; first deliver All Time, Year, and Month.
- Add transaction and reporting-data export; consider historical spreadsheet migration separately.

## Later, only when useful

- Debt records and balance snapshots, including optional extra-paydown goals.
- Recurring bills and subscriptions.
- Additional CSV formats, import profiles, statement parsing, and automatic bill matching.
- Backup/restore.

Keep the initial product local-only. Docker Compose is for local development only; do not introduce cloud services, telemetry, bank syncing, or production infrastructure.
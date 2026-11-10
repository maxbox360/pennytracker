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

## Milestone 4 — Transparent categorization rules

- Add visible, editable, disableable categorization rules scoped to `EXPENSE`; the initial match is raw description contains only.
- Normalize matching values by trimming, collapsing whitespace, and case-folding. Do not strip punctuation, tokenize, or fuzzy-match.
- Apply usable rules after overlap detection in import preview and recompute during confirmation. Snapshot the target Subcategory's current bucket and retain lightweight rule provenance.
- Never infer financial classification. Same-target matches may categorize, with the lowest-ID match retained only as representative provenance; conflicting targets remain uncategorized and require Review with the conflict shown in preview and Review reasons.
- Offer deliberate Review opt-in with an editable exact match value. Add a Rules screen for CRUD, archived-target warnings, enable/disable, and safe removal.
- Offer explicit server-side application to matching pending uncategorized Expenses across all pages. Never overwrite assigned categories or clear unrelated blockers; recompute review status and apply atomically.
- Manual category edits clear rule provenance. Rule edits, disabling, and removal do not rewrite historical assignments or bucket snapshots.
- Do not add merchant matching, accounting/reporting calculations, transfer pairing, savings calculations, debts, recurring expenses, or other later features.

## Milestone 5 — Accounting calculations and monthly reporting

- Add an explicit `NONE` / `CONTRIBUTION` / `WITHDRAWAL` Savings effect to Transfers. A non-NONE effect requires a Savings Subcategory; a Savings Subcategory alone does not qualify. Exactly one chosen transaction represents each Savings effect. Do not pair transfers automatically.
- Sum signed `INCOME` amounts. For eligible Needs/Wants Expenses and Refunds, contribute `-amount` using the transaction's historical bucket snapshot. Refunds offset the bucket in the Refund's own month; do not link refunds.
- Calculate Savings contributions and withdrawals separately using the absolute ledger amount. Net Savings activity is contributions minus withdrawals. Remaining / Unallocated is signed Income minus Needs, Wants, and Savings contributions; withdrawals do not increase it.
- Use `Transaction.date` for every report period and drilldown; never use posted date, import batches, or filenames to choose the month.
- Exclude explicit exclusions, confirmed/unresolved duplicates, unresolved rule conflicts, UNKNOWN, uncategorized Expenses/Refunds, and invalid Savings effects. Surface unresolved blockers and do not treat `PENDING` as a universal exclusion.
- Persist one current database allocation-target configuration (50/30/20 defaults; independent 0–100% values need not sum to 100%). Use current targets for all months and do not mutate settings when viewing reports.
- Add a dense monthly Reports screen with month navigation, target/actual/percentage-point comparison, Subcategory breakdowns, completeness warnings, and paginated drilldowns that reconcile exactly to report amounts.
- Do not add year/All-Time reports, trends, custom ranges, snapshots/effective-dated targets, transfer pairing, linked refunds, or Subcategory budgets.

## Milestone 6 — Historical reporting (core completion)

- Generalize the existing accounting rules once for Monthly, Year, and All Time; preserve Monthly API behavior and accounting parity.
- Add All Time and calendar-year reports, including all 12 months in a selected year and a continuous All-Time monthly series from the earliest through latest transaction month.
- Aggregate totals, periods, ranked subcategories, trends, completeness, and paginated drilldowns in the database where practical; retain Decimal/signed accounting behavior.
- Preserve historical bucket snapshots when grouping category history. Rank subcategories by signed net Expense/Refund contribution, including negative totals.
- Use aggregate-period ratios for percentages and current allocation targets as clearly labeled reference lines, not historical targets.
- Keep incomplete periods and distinguish them from truly empty and known-zero periods. Provide auditable drilldowns at All Time, Year, and Month levels.
- Make Reports default to All Time with All Time → Year → Month navigation. Use separate native-SVG dollar and percentage charts with accompanying value tables.
- Do not add a migration. If a genuine schema requirement emerges, pause and get approval first.
- Do not add export, year-over-year percentage comparisons, custom ranges, transfer pairing, linked refunds, spreadsheet migration, or other post-core features.
- Once complete, consider the original core finished: import → categorization → Review → monthly accounting → historical reporting → auditable drilldown.

## Deferred post-core enhancements

- Transaction and reporting-data export.
- Debt records and balance snapshots, including optional extra-paydown goals.
- Recurring bills and subscriptions.
- Additional CSV formats, import profiles, statement parsing, and automatic bill matching.
- Historical spreadsheet migration and backup/restore.

## Post-core accounting refinement — Other Inflows (complete)

- Added `OTHER_INFLOW` for genuinely new money that is not normal budgeting Income, a Refund, or a Transfer. It is signed-summed as a separate Month/Year/All-Time report line with transaction drilldowns.
- Kept Needs/Wants/Savings as the only budget buckets. Added a separate Other Inflow scope for optional descriptive Subcategories; existing Subcategories migrate as budget-scoped without changing bucket snapshots.
- Kept Other Inflows out of Income allocation percentages, Remaining / Unallocated, and the monthly allocation donut. UNKNOWN incoming transactions remain unresolved until explicitly classified.
- Kept Refund and Transfer accounting unchanged. Categorization rules remain Expense-only; Other Inflow rules, P2P inference, Net Cash Flow, transfer pairing, and gambling analytics remain deferred.
- Added an Other Inflows group to Categories and classification-aware optional Subcategory assignment in the transaction editor.

## Post-core reporting refinement — Flexible ranges

- Add calendar-based YTD, 7-day, 30-day, and inclusive custom ranges alongside the existing All Time → Year → Month hierarchy.
- Reuse the shared accounting, current targets, completeness, allocation donut, and server-paginated drilldowns. Group short-range trends by day and longer ranges by month; only transactions within the selected dates contribute.
- Keep saved ranges, prior-period comparisons, export, and other post-core features out of scope.

## Later, only when useful

- Debt records and balance snapshots, including optional extra-paydown goals.
- Recurring bills and subscriptions.
- Additional CSV formats, import profiles, statement parsing, and automatic bill matching.
- Backup/restore.

Keep the initial product local-only. Docker Compose is for local development only; do not introduce cloud services, telemetry, bank syncing, or production infrastructure.
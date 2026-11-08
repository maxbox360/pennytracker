# Personal Finance App — Agent Instructions

## Project purpose

This repository contains a local-only personal finance application.

The primary workflow is:

**Import transactions → automatically categorize known transactions → review unknown transactions → understand spending over time.**

The application should make an existing manual spreadsheet workflow faster, easier to audit, and easier to explore.

This is a personal application for a single user.

Do not design it as a SaaS product.

---

# Technology stack

Unless the user explicitly decides otherwise, use:

## Backend

- Python
- Django
- Django REST Framework
- SQLite

## Frontend

- React
- TypeScript
- Vite

Use a lightweight charting library such as Recharts if charts are needed.

Do not replace these technologies without discussing the change first.

---

# Development philosophy

Prefer the simplest implementation that correctly solves the problem.

Prioritize:

1. Correctness
2. Readability
3. Maintainability
4. Transparency
5. User experience
6. Performance

Performance optimization is not important unless an actual performance problem exists.

Avoid speculative abstractions.

Do not introduce infrastructure simply because it is common in large production applications.

---

# Do not overengineer this project

Do not introduce any of the following unless the user explicitly requests them or a demonstrated technical requirement makes them necessary:

- Microservices
- Kubernetes
- Redis
- Celery
- Message queues
- GraphQL
- PostgreSQL
- Cloud infrastructure
- OAuth
- Multi-user authentication
- Complex permissions
- Event-driven architecture
- Repository/service layers that merely wrap Django ORM calls
- Dependency injection frameworks
- Premature caching
- CQRS
- Event sourcing

Do not create abstractions for hypothetical future requirements.

This application runs locally for one person.

SQLite is the expected database.

---

# Docker

Use Docker Compose as the recommended local development workflow. The Compose file must be named `docker-compose.yaml`.

Keep Docker limited to two development services: `backend` (Django/DRF) and `frontend` (React/Vite). Continue using SQLite and bind source code for reloads. Bind published ports to localhost. Do not add production Docker stacks or infrastructure services such as PostgreSQL, Redis, Celery, nginx, Kubernetes, or message queues.

The application should remain runnable directly through local Python and Node environments as an alternative.

---

# Financial correctness is critical

Financial calculations must be deterministic and easy to audit.

Never use binary floating-point values for money.

On the Django side:

- Use `Decimal`
- Use `DecimalField` for currency values

Establish and document a consistent convention for positive and negative transaction amounts.

Do not silently change that convention later.

Important financial logic should have automated tests.

---

# Primary budgeting model

The primary budget categories are:

- Needs
- Wants
- Savings

The user's target allocation is approximately:

- 50% Needs
- 30–32% Wants
- 20% Savings

These percentages must remain configurable.

Do not hard-code them into business logic.

---

# Savings definition

This is a confirmed product decision.

**Savings means actual savings activity.**

Savings is NOT calculated as:

`Income - Needs - Wants`

Examples of qualifying Savings activity may include:

- Money transferred to a designated savings account
- Emergency fund contributions
- Investment contributions
- Other explicitly designated savings goals

Money that simply remains in checking does not automatically count as Savings.

The dashboard must distinguish:

- Income
- Needs
- Wants
- Savings
- Remaining / Unallocated

Conceptually:

`Remaining / Unallocated = Income - Needs - Wants - Savings`

Do not silently classify Remaining / Unallocated money as Savings.

---

# Transfers

Internal transfers require special handling.

Moving money between accounts owned by the user should not normally create spending.

Examples:

Checking → Credit Card payment:
- Do not count the payment as spending if the individual credit card purchases are already represented.

Checking → Savings:
- Do not count as Needs or Wants.
- It may count as Savings if the destination or transaction is designated as a qualifying savings action.

Checking → Checking:
- Normally exclude from spending and income calculations.

Avoid double-counting transactions.

The exact transfer model should remain understandable and auditable.

---

# Credit cards

Credit card purchases are spending.

Credit card payments are generally transfers, not new spending.

For example:

A $50 restaurant purchase on a credit card:

`Restaurants → Wants → $50 spending`

Later paying $50 from checking to the credit card:

`Internal transfer → $0 additional spending`

Never count both as spending.

---

# Refunds

Refund behavior should be transparent.

Where possible, a refund should offset the category associated with the original purchase.

Do not treat a normal purchase refund as ordinary income unless explicitly configured that way.

If implementation details are ambiguous, discuss them before changing financial behavior.

---

# Transaction transparency

Never destroy imported source data.

Transactions should preserve the original bank-provided description.

A cleaned merchant name may exist separately.

Example:

Raw:

`SQ *LOCAL COFFEE ROA 479123456`

Clean merchant:

`Local Coffee Roaster`

Preserve both.

---

# Categorization hierarchy

The normal spending hierarchy is:

`Transaction → Subcategory → Budget Category`

Examples:

`Exxon → Fuel → Needs`

`Restaurant → Restaurants → Wants`

`Savings transfer → Emergency Fund → Savings`

Subcategories are user-configurable.

Do not permanently hard-code specific subcategories into application logic.

Seed data is acceptable if it can later be edited or removed.

---

# Categorization rules

Automatic categorization should remain rule-based and transparent.

Examples:

`description contains "STARBUCKS" → Coffee`

`merchant equals "Netflix" → Subscriptions`

Start with simple matching.

Do not add machine-learning categorization or external AI services unless explicitly requested.

Rules must be visible and editable by the user.

The user should be able to understand why a transaction was categorized.

---

# Review Queue

The Review Queue is a core workflow, not an optional extra.

The transaction import workflow should conceptually be:

1. Parse imported file.
2. Normalize transactions.
3. Detect duplicates.
4. Apply categorization rules.
5. Automatically accept clear rule matches.
6. Send unmatched transactions to the Review Queue.
7. Let the user quickly categorize remaining transactions.

The long-term goal is:

**Upload CSV → review a small number of unknown transactions → dashboard ready.**

Design features around reducing repetitive review work.

---

# Bulk operations

Where appropriate, transaction-management screens should support bulk operations.

Examples:

- Categorize several transactions
- Assign a merchant
- Exclude transactions
- Create a category rule

Do not force repetitive one-at-a-time editing when a simple bulk operation is reasonable.

---

# Duplicate imports

Uploading the same statement twice must not duplicate transactions.

Duplicate detection is important business logic and must have tests.

Do not assume every financial institution provides a unique transaction ID.

Use the most reliable available combination of transaction attributes and import metadata.

---

# Import architecture

CSV is the initial import format.

Imports may contain any date range: a month, multiple months, year-to-date, a calendar year, multiple years, or an arbitrary statement range. ImportBatch records provenance and never defines a reporting period. Transaction dates, not filenames or batch boundaries, determine reporting periods.

Keep source-specific parsing in the concrete parser/format-recognition layer and keep PennyTracker transactions, batches, endpoints, categories, and reporting source-independent. Do not tightly couple transaction importing to one financial institution.
Prefer a normalized transaction format and configurable import mappings.

Potential normalized fields include:

- Transaction date
- Posted date
- Raw description
- Merchant
- Amount
- Account
- External transaction ID when available

The first implementation can remain simple.

Do not build a plugin framework for importers unless multiple real formats demonstrate that it is necessary.

Exact-file hashes prevent identical re-imports only. Overlapping but non-identical exports remain a known limitation until transaction-level duplicate handling is implemented; never discard similar-looking transactions silently.

---

# Historical integrity

Historical reports should not unexpectedly change because a current setting changed.

Be particularly careful with:

- Monthly budgets
- Budget target percentages
- Debt balances
- Recurring payment amounts

Use snapshots or month-specific records where they provide clear value.

Do not implement a generalized temporal database.

---

# Debt balances

Debt balance updates should preserve history.

Do not simply overwrite the previous balance.

Use balance snapshots so debt progress can be shown over time.

Financial projections or credit-score predictions are outside the initial scope.

---

# Dashboard principles

Dashboards should emphasize useful information rather than decoration.

Use:

- Summary cards
- Tables
- Simple charts
- Drill-down links
- Clear visual hierarchy

Avoid:

- Excessive charts
- Decorative animations
- Large amounts of empty dashboard chrome
- Bright spreadsheet-style cell coloring

Charts should answer a question.

If a table communicates something more clearly, use a table.

Reporting should support All Time, calendar year, and month views, with trends over time and drill-down from aggregates to their contributing transactions. Custom date ranges should remain straightforward but need not be implemented before these core periods. Reporting periods are independent of ImportBatch boundaries and use transaction dates.

---

# Drill-down behavior

Calculated values should be auditable.

If the UI displays:

`Fast Food: $351.50`

the user should be able to inspect the transactions contributing to that number.

If it displays:

`Needs: 53.4%`

the user should be able to inspect the categories and transactions included.

If it displays:

`Savings: $900`

the qualifying savings activity should be inspectable.

Avoid opaque aggregate values.

---

# Frontend guidelines

Use TypeScript.

Prefer small, understandable React components.

Do not create a complex global state architecture unless application behavior actually requires it.

Prefer server state/query tools or simple React state over large state-management frameworks.

Avoid Redux unless there is a demonstrated need.

Keep API interaction centralized enough that request behavior is consistent.

All frontend API mutations must use the shared CSRF-aware `apiFetch` in `frontend/src/api.ts`. Do not use raw `fetch()` for POST, PATCH, PUT, or DELETE requests.

Desktop usage is the priority.

Responsive behavior is desirable but mobile-first design is not required.

---

# Django guidelines

Use Django conventions whenever they adequately solve the problem.

Prefer:

- Django ORM
- Django migrations
- DRF serializers
- DRF views/viewsets where appropriate
- Django admin where it provides useful development/admin functionality

Avoid rebuilding features Django already provides.

Keep business logic out of oversized views.

Important financial calculations should live in clearly named functions/services/domain helpers that are easy to test.

Do not create layers solely for architectural purity.

---

# API guidelines

Keep the REST API predictable.

Use conventional resource-oriented endpoints unless a workflow requires a specific action endpoint.

Examples of reasonable resources:

- accounts
- transactions
- categories
- rules
- imports
- budgets
- debts
- recurring expenses

Avoid unnecessary API complexity.

---

# Testing guidelines

Prioritize tests around business rules rather than visual implementation details.

Important test areas include:

- Duplicate detection
- Categorization rules
- Needs calculations
- Wants calculations
- Savings calculations
- Remaining / Unallocated
- Refund handling
- Transfer handling
- Credit card payments
- Savings transfers
- Monthly aggregation
- Yearly aggregation
- Historical budget behavior

Frontend snapshot tests are low priority.

---

# Privacy

Financial information must remain local in the initial application.

Do not:

- Send transaction data to third-party services
- Add analytics
- Add telemetry
- Add advertising
- Integrate cloud financial APIs
- Integrate Plaid
- Upload financial data externally

Any future external integration must be explicitly approved.

---

# Security scope

This is a local single-user application.

Do not add login/authentication merely because financial data is present.

If a specific threat model later requires local authentication or encryption, discuss it with the user first.

Do not give the appearance of security by adding unnecessary authentication while leaving the underlying local database unchanged.

---

# Code changes

When making substantial changes:

1. Inspect the existing implementation first.
2. Preserve established project conventions.
3. Explain architectural changes before making them.
4. Prefer modifying existing code over creating competing implementations.
5. Do not duplicate functionality.
6. Remove obsolete code when a replacement is complete.
7. Run relevant tests after changes.
8. Fix failures caused by your changes before considering the task complete.

---

# Dependencies

Before adding a dependency:

1. Determine whether the language/framework already handles the requirement adequately.
2. Explain what the dependency gives us.
3. Prefer mature, lightweight dependencies.
4. Avoid dependencies for trivial functionality.

Do not replace working dependencies without a concrete reason.

---

# Database migrations

Model changes must include appropriate Django migrations.

Do not manually modify SQLite database structure.

Do not destroy existing local financial data as part of a migration unless the user explicitly approves a destructive change.

---

# Development workflow

Build incrementally.

Do not attempt to implement the entire product in one pass.

A feature should generally progress through:

1. Confirm behavior.
2. Design the smallest reasonable implementation.
3. Implement backend/domain logic.
4. Test important business rules.
5. Implement API.
6. Implement UI.
7. Verify the workflow end-to-end.
8. Move to the next feature.

Keep the project runnable between milestones.

---

# When requirements are ambiguous

For minor implementation details:

Use a sensible conventional default.

For decisions that materially affect:

- Financial calculations
- Data models
- Historical data
- User workflow
- Privacy
- Major dependencies
- Architecture

Explain the alternatives and ask the user before committing to a difficult-to-reverse choice.

Do not repeatedly ask about trivial implementation details.

---

# Scope discipline

When implementing a requested feature, do not opportunistically build unrelated features.

It is acceptable to point out a useful future enhancement.

Do not implement that enhancement unless it is needed for the current feature or the user asks for it.

---

# Core product principle

The existing spreadsheet already works.

The application earns its value by making the workflow:

- Faster
- Less repetitive
- Easier to understand
- Easier to audit
- More automated
- More useful over time

The central experience should remain:

**Import my transactions, review the handful that need attention, and immediately understand where my money went.**
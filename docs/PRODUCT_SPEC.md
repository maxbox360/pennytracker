I want you to help me design and build a lightweight personal finance application using **Django for the backend and React for the frontend**.

This is a personal project that will run locally on my computer. It does **not** need to be designed as a production SaaS application, support multiple users, scale to thousands of users, or include enterprise infrastructure.

I want the code to be clean, maintainable, understandable, and reasonably well-tested, but I specifically want to avoid unnecessary overengineering.

I currently track all of this manually in a Google Sheet. I have attached a screenshot of my current spreadsheet for reference. The application does not need to reproduce the spreadsheet visually. I would prefer that we improve upon it and create a cleaner dashboard-style interface.

# How I want you to work with me

Please act as both:

- A senior full-stack developer
- A product/design collaborator

Do **not** immediately generate the entire application.

Before coding:

1. Review the requirements below.
2. Identify important product or technical decisions that still need to be made.
3. Point out any assumptions you would otherwise have to make.
4. Suggest improvements where you think the application could work better.
5. Ask me questions where my preference genuinely matters.
6. Recommend sensible defaults where I probably do not need to make a decision myself.
7. Propose a simple architecture and development roadmap.

Once we agree on the major decisions, help me build the application incrementally.

For each significant feature:

- Explain the approach before implementing it.
- Keep the architecture simple.
- Prefer readable code over clever code.
- Explain meaningful tradeoffs when several approaches are reasonable.
- Avoid adding dependencies unless they provide meaningful value.
- Add tests for important business logic.
- Keep the application runnable throughout development.
- Do not generate huge amounts of disconnected code at once.
- Keep track of decisions and features we have already implemented so we do not repeatedly redesign established parts of the project.

---

# Core application concept

The application should allow me to import transaction data from my bank across arbitrary date ranges and provide a detailed analysis of my spending.

The basic hierarchy should be:

**Transaction → Subcategory → Budget Category**

Examples:

- Exxon → Fuel → Needs
- Walmart Grocery → Groceries → Needs
- Local restaurant → Restaurants → Wants
- JCPenney → Clothing → Wants
- Rent payment → Rent → Needs
- Transfer to savings → Savings → Savings

The three major budget categories are:

- **Needs**
- **Wants**
- **Savings**

My target spending allocation is approximately:

- Needs: **50%**
- Wants: **30–32%**
- Savings: **20%**

These percentages must be configurable rather than hard-coded.

---

# Confirmed financial model

## Savings means actual savings activity

This is an important requirement.

Do **not** automatically calculate Savings as:

`Income - Needs - Wants`

Savings should represent money that I actually moved toward a savings or investment goal.

Examples:

- Transfer to a savings account
- Emergency fund contribution
- Investment contribution
- Transfer to another account that has explicitly been designated as savings
- Other explicitly defined savings goals

Therefore, the monthly financial summary should distinguish between:

- Income
- Needs
- Wants
- Savings
- Remaining / Unallocated

For example:

Income: $6,000  
Needs: $3,000  
Wants: $1,500  
Savings: $900  
Remaining / Unallocated: $600

The $600 is **not** automatically Savings.

Conceptually:

`Remaining / Unallocated = Income - Needs - Wants - Savings`

This is important because I want the application to reflect actual financial behavior rather than assuming that money left sitting in checking was saved.

Transfers between my own accounts will therefore require thoughtful handling.

A legitimate savings transfer should be able to count toward the Savings category without being treated as ordinary spending or accidentally counted twice.

We should finalize the exact transaction model before implementing this.

---

# Recommended initial technology stack

Unless we decide otherwise during planning, my current preference is:

## Backend

- Python
- Django
- Django REST Framework
- SQLite

SQLite should be more than adequate because this is a local single-user application.

## Frontend

- React
- TypeScript
- Vite

For charts, recommend a lightweight React charting library such as Recharts if appropriate.

For styling, recommend something that will produce a modern dashboard without introducing unnecessary complexity.

I am open to recommendations before we commit to specific frontend libraries.

---

# 1. Accounts

The application should support multiple financial accounts.

Examples:

- Checking
- Savings
- Credit card
- Loan
- Investment account

An account could contain fields such as:

- Name
- Institution
- Account type
- Last four digits or nickname
- Active/inactive
- Optional current balance
- Whether transfers into the account count as Savings
- Notes

Do not store full bank account numbers or other unnecessary sensitive information.

Transactions should be associated with an account when possible.

We should discuss whether account balances themselves should be tracked in version 1 or whether balances should primarily live in the Debt section.

---

# 2. Transaction importing

I want to upload transaction files exported by my bank or credit card companies.

CSV should be supported first.

Eventually I may want to support multiple institutions whose CSV formats differ.

The importer should therefore allow incoming columns to be mapped to normalized application fields such as:

- Transaction date
- Posted date
- Description
- Merchant
- Amount
- Debit/credit indicator
- Account

Avoid tightly coupling the application to one bank's export format.

Ideally we can eventually save import profiles for different institutions.

For example:

`Bank of Example Checking CSV`

might map:

`Posting Date → posted_date`  
`Description → raw_description`  
`Amount → amount`

while another institution may use completely different headings.

---

# 3. Import batches and duplicate detection

Every upload should create an **Import Batch**.

An import batch should record useful metadata such as:

- Filename
- Import date/time
- Account
- Number of transactions imported
- Number skipped as duplicates
- Number automatically categorized
- Number requiring review

The application must detect duplicate transactions so that importing the same statement twice does not duplicate spending.

Please recommend a practical duplicate-detection strategy.

Potential fields may include:

- Account
- Transaction date
- Amount
- Raw description
- External transaction ID if available

Do not assume that every bank provides a unique transaction ID.

---

# 4. Categories and subcategories

Every normal spending transaction should be assignable to a **subcategory**.

Example subcategories include:

## Needs

- Rent
- Electric
- Internet
- Phone
- Car Payment
- Car Insurance
- Car Maintenance
- Fuel
- Groceries
- Medical
- Student Loans
- Taxes

## Wants

- Restaurants
- Fast Food
- Coffee
- Entertainment
- Clothing
- Electronics
- Books
- Gifts
- Subscriptions
- Travel
- Dating
- Home Goods
- Games
- Hobbies

## Savings

- Savings Account
- Emergency Fund
- Investments
- House Fund
- Other Savings Goals

These are examples only.

Subcategories must not be permanently hard-coded.

I want to be able to:

- Create subcategories
- Rename subcategories
- Edit them
- Archive them
- Reassign them
- Give them optional monthly budget goals

Each subcategory belongs to one of:

- Needs
- Wants
- Savings

Imported transactions may also temporarily be:

- Uncategorized
- Transfer
- Income
- Excluded

depending on the transaction model we ultimately choose.

---

# 5. Automatic categorization rules

This is one of the most important features.

I want the application to remember categorization decisions so I do not have to recategorize the same merchants every month.

Example:

If I categorize:

`STARBUCKS`

as:

`Coffee → Wants`

I should have the option to create a rule such as:

> If merchant or description contains "STARBUCKS", categorize as Coffee.

Other examples:

`CASEY'S → Fuel`  
`NETFLIX → Subscriptions`  
`CHICK-FIL-A → Fast Food`  
`CARROLL ELECTRIC → Electric`

Rules should be visible and editable.

Potential rule matching might support:

- Description contains
- Merchant contains
- Exact merchant match

Start simple.

Do **not** build an AI or machine-learning categorization engine for version 1.

Transparency is more important than sophistication.

---

# 6. Transaction review queue

Imported transactions should follow this workflow:

1. Import transactions.
2. Detect duplicates.
3. Apply existing categorization rules.
4. Automatically categorize transactions with matching rules.
5. Send unmatched or ambiguous transactions to a **Review Queue**.

The Review Queue should be designed for quick processing.

For every transaction, show useful information such as:

- Date
- Raw bank description
- Clean merchant name
- Amount
- Account
- Current category
- Suggested category if applicable
- Notes

From the Review Queue I should be able to:

- Assign a subcategory
- Edit the cleaned merchant name
- Add notes
- Mark something as a transfer
- Mark something as income
- Mark something as a refund
- Exclude something from budget calculations
- Create a categorization rule
- Finish reviewing the transaction

Bulk actions should be supported.

For example, if an import contains eight transactions from the same restaurant, I should be able to select all of them and categorize them at once.

The ideal long-term monthly workflow should become:

**Upload CSV → most transactions categorize automatically → review a small number of unknown transactions → dashboard is ready.**

Reducing repetitive manual categorization is a major goal of this project.

---

# 7. Merchant normalization

Bank descriptions are often ugly.

For example:

`SQ *LOCAL COFFEE ROA 4791234567 AR`

might really mean:

`Local Coffee Roaster`

I want to preserve both:

- The original/raw transaction description
- A cleaner merchant/display name

The raw description should never be destroyed.

Categorization rules may operate on either the raw description or cleaned merchant.

---

# 8. Transaction list

I want a complete transaction screen.

It should support:

- Search
- Filter by date range
- Filter by month
- Filter by year
- Filter by account
- Filter by major category
- Filter by subcategory
- Filter by merchant
- Filter by transaction type
- View only uncategorized transactions
- Sort by date
- Sort by amount

I should be able to edit:

- Category
- Merchant
- Notes
- Transaction classification
- Whether the transaction is included in budget calculations

Clicking a transaction should show the relevant details without requiring a complicated workflow.

---

# 9. Special transaction types

Please help me design sensible handling for:

- Income/paychecks
- Refunds
- Returns
- Reimbursements
- Credit card payments
- Transfers between my own accounts
- Savings transfers
- Cash withdrawals
- Loan payments
- Investment transfers

This is especially important because I do not want internal transfers or credit card payments to distort spending.

For example:

If I import the individual purchases from my credit card and later import the $1,500 checking-account payment to that card, the $1,500 payment should **not** count as another $1,500 of spending.

Likewise:

Moving $1,000 from checking to savings should not appear as $1,000 of ordinary spending.

However, if the Savings account has been designated as a savings destination, that transfer may count toward the monthly Savings goal.

I do not need full double-entry accounting, but calculations need to be trustworthy.

Please design the simplest transaction model that handles these cases correctly.

---

# 10. Income

Income should be trackable through transactions.

Examples:

- Paychecks
- Bonuses
- Refund income where appropriate
- Other income

Income should be separate from spending categories.

For each month, calculate total qualifying income.

I should also be able to manually correct or exclude unusual transactions if an import labels something incorrectly.

---

# 11. Monthly dashboard

I want a dedicated dashboard for each month.

Examples:

- January 2026
- February 2026
- March 2026

The monthly dashboard should prominently show:

## Income

Total qualifying income for the month.

## Spending

Total Needs + Wants spending.

Depending on the final accounting model, Savings may be displayed separately from spending.

## Savings

Actual qualifying Savings activity.

## Remaining / Unallocated

Income minus Needs, Wants, and Savings.

## Needs / Wants / Savings summary

For each major category, show:

- Target percentage
- Target dollar amount
- Actual percentage
- Actual dollar amount
- Difference from target

Example:

| Category | Target | Actual | Difference | Actual % |
| Needs | $3,000 | $3,400 | -$400 | 56.7% |
| Wants | $1,800 | $1,400 | +$400 | 23.3% |
| Savings | $1,200 | $900 | -$300 | 15.0% |

Also show:

`Remaining / Unallocated: $300`

Use visual indicators to make over-target and under-target areas obvious without making the dashboard obnoxiously colorful.

---

# 12. Allocation chart

I want a visual representation of Needs, Wants, and Savings.

My current preference is:

Use a donut/pie chart for:

- Needs
- Wants
- Savings

Do **not** quietly include Remaining / Unallocated as Savings.

Remaining / Unallocated should likely be displayed as a separate card or indicator near the chart.

Please critique this approach and recommend another visualization only if it communicates the information more clearly.

The chart should display:

- Percentage
- Dollar amount

If useful, it should also show my target allocation for comparison.

Clicking a section should ideally drill down into the related subcategories or transactions.

---

# 13. Actionable budget insights

Where calculations are straightforward and transparent, I would like useful contextual information.

Example:

Income: $6,000  
Savings target: 20% = $1,200  
Actual Savings: $900  
Remaining / Unallocated: $600

The application could display something such as:

`You are $300 short of your 20% savings target. You currently have $600 remaining unallocated.`

Keep insights deterministic and transparent.

Do not build an opaque financial-advice engine.

---

# 14. Subcategory analysis

Under the major budget summary, I want a breakdown of spending by subcategory.

Example:

## Needs

Rent: $1,495  
Groceries: $428  
Fuel: $103  
Electric: $148  
Medical: $399

## Wants

Fast Food: $352  
Restaurants: $125  
Coffee: $80  
Entertainment: $60

For each subcategory show useful information such as:

- Actual spending
- Optional monthly budget goal
- Difference
- Remaining budget
- Percentage of major category
- Percentage of total income

Clicking a subcategory should show the underlying transactions.

---

# 15. Monthly budget goals

I want optional spending goals for subcategories.

Examples:

Groceries: $600  
Fuel: $120  
Fast Food: $200  
Coffee: $60  
Restaurants: $150

Not every subcategory needs a goal.

For subcategories with goals, show:

- Goal
- Actual
- Remaining
- Amount over budget

Budget goals should preserve history.

Changing my grocery goal in December should not rewrite what the goal was in February.

Please recommend whether this should use:

- Monthly budget records
- Effective date ranges
- A template copied into each month

Prefer the simplest maintainable solution.

---

# 16. Yearly dashboard

I want a yearly view showing running totals across the selected year.

Show:

- Total income
- Total Needs
- Total Wants
- Total Savings
- Total Remaining / Unallocated if meaningful
- Total spending
- Average monthly spending
- Average Needs/Wants/Savings percentages
- Savings rate

Show totals by subcategory.

For example:

Restaurants:

January: $140  
February: $95  
March: $160  
YTD: $395  
Monthly average: $131.67

Useful charts might include:

- Needs/Wants/Savings by month
- Total spending by month
- Savings by month
- Remaining / Unallocated by month
- Subcategory spending trends
- YTD allocation
- Spending-category changes over time

Recommend only charts that provide useful information.

Do not add charts merely because a chart library is available.

---

# 17. Month-to-month comparisons

I would like the application to make it easy to compare months.

Examples:

`Fast Food spending decreased $86 compared with last month.`

`Electric increased $21.`

`Savings rate increased from 14% to 21%.`

Possible useful comparisons:

- Previous month
- Three-month average
- Year-to-date monthly average

Keep this simple and based on actual calculated data.

---

# 18. Debts

I want a dedicated **Debts** section.

Debt types may include:

- Credit cards
- Student loans
- Personal loans
- Auto loans
- Other loans

Each debt could contain:

- Name
- Lender
- Debt type
- Current balance
- Interest rate/APR
- Minimum payment
- Original balance if known
- Credit limit if applicable
- Due date
- Promotional APR expiration date if applicable
- Active/inactive status
- Notes

I should be able to manually update balances.

---

# 19. Debt balance history

Do not simply overwrite the previous debt balance.

Maintain balance history through snapshots.

For example:

| Date | Balance |
| Jan 1 | $8,500 |
| Feb 1 | $8,100 |
| Mar 1 | $7,650 |

This should support a debt balance trend chart.

Useful information may include:

- Current balance
- Original balance
- Amount paid down
- Percentage paid down
- Balance change since previous snapshot

Do not attempt to predict credit scores or provide complicated payoff optimization unless we explicitly add that later.

---

# 20. Student loans

Student loans may contain multiple individual loans under one servicer.

Please model this cleanly.

For example:

Servicer:

`Example Student Loan Servicer`

Loans:

Loan AA  
Loan AB  
Loan AC

Each individual loan may have:

- Balance
- Interest rate
- Minimum payment if applicable
- Notes

The debt dashboard should be able to show both:

- Individual loan details
- Combined servicer balance

---

# 21. Credit card statement imports

Eventually, I may want to upload monthly credit card statements and have the application extract or update:

- Current balance
- Minimum payment
- Interest rate
- Statement date
- Due date

This can come later.

Do **not** complicate the initial architecture significantly just to support future PDF parsing.

The first version can use manual debt updates.

---

# 22. Monthly bills

I want to track recurring bills such as:

- Rent
- Electricity
- Internet
- Phone
- Insurance
- Car payment
- Student loan payment

Each bill could contain:

- Name
- Typical amount
- Due date
- Subcategory
- Major category
- Auto-pay yes/no
- Active/inactive
- Notes

Eventually, imported transactions could be matched against expected bills.

Example:

Expected bill:

`Carroll Electric — approximately $150`

Imported transaction:

`CARROLL ELECTRIC — $147.53`

The application could then mark the month's bill as paid.

This matching does not need to be part of the first implementation if it makes the MVP unnecessarily complicated.

---

# 23. Monthly subscriptions

I want a section for recurring monthly subscriptions.

Examples:

- Streaming services
- Patreon
- Software subscriptions
- Memberships

Fields may include:

- Name
- Amount
- Billing date
- Subcategory
- Major category
- Active/inactive
- Notes

Show:

- Total monthly subscription cost
- Estimated annual subscription cost

Eventually transaction imports could automatically recognize these.

---

# 24. Annual subscriptions

I also want annual subscriptions.

Examples:

- Amazon Prime
- Password manager
- AAA
- Memberships
- Annual software subscriptions

Fields may include:

- Name
- Amount
- Renewal date
- Category
- Auto-renew yes/no
- Active/inactive
- Notes

Show:

- Total annual subscription cost
- Upcoming renewals
- Effective monthly cost

For example:

`$120/year = $10/month effective cost`

---

# 25. Recurring payment calendar

A useful future enhancement may be a simple calendar/timeline showing:

- Bills due
- Monthly subscriptions
- Annual renewals
- Debt payments

Please determine whether this belongs in the MVP or should be added later.

---

# 26. Historical data integrity

The application should preserve useful historical information.

If I:

- Rename a category
- Change a budget goal
- Change my Needs/Wants/Savings targets
- Update a debt balance
- Change a subscription price
- Change a recurring bill amount

I do not necessarily want historical reports to silently change.

Please identify which data should use:

- Historical snapshots
- Monthly records
- Effective dates

versus which values can safely reference the current configuration.

Do not create an overly complicated temporal database.

Use history only where it produces meaningful reporting benefits.

---

# 27. Data transparency

This is very important.

Whenever the application shows a calculated value, I want it to be reasonably easy to understand where that number came from.

Example:

If the dashboard says:

`Fast Food: $351.50`

I should be able to click that value and see the transactions totaling $351.50.

If the dashboard says:

`Needs: 53.4%`

I should be able to inspect the Needs subcategories and transactions included in that calculation.

If Savings says:

`$900`

I should be able to see the transactions that counted as Savings.

Avoid opaque calculations.

---

# 28. Navigation

One possible navigation structure is:

## Dashboard

Current month overview.

## Transactions

All imported transactions and categorization.

## Review

Uncategorized/new transactions requiring attention.

## Months

Historical monthly dashboards.

## Year

Year-to-date and yearly analysis.

## Debts

Credit cards, loans, student loans, etc.

## Recurring

- Bills
- Monthly subscriptions
- Annual subscriptions

## Categories

Manage subcategories and their Needs/Wants/Savings classification.

## Rules

Manage automatic categorization rules.

## Settings

- Budget percentages
- Account settings
- Import profiles
- Other preferences

Please critique this navigation and recommend improvements before implementing it.

---

# 29. Dashboard visual design

I want the interface to feel like a modern personal finance dashboard.

I prefer:

- Clean
- Modern
- Information-dense without feeling cluttered
- Clear typography
- Useful summary cards
- Good tables for detailed information
- Charts only where appropriate
- Consistent spacing
- Good visual hierarchy
- Desktop-first layout

Responsive behavior is useful, but mobile optimization is not a major priority.

Do not reproduce the spreadsheet's bright cell-by-cell coloring.

Instead use color intentionally for:

- Needs
- Wants
- Savings
- Positive values
- Negative values
- Warnings
- Uncategorized items

Needs/Wants/Savings should have consistent colors throughout the entire application.

---

# 30. Drill-down behavior

Summary information should generally be clickable.

Examples:

Click:

`Needs — $3,421`

and see either:

- Needs subcategories

or:

- All Needs transactions

Click:

`Fast Food — $351`

and see the transactions.

Click a chart segment and filter or navigate to the corresponding transactions.

The dashboard should act as an entry point into the underlying data, not merely display static numbers.

---

# 31. Data export

Because this application contains my financial history, I do not want my data trapped inside it.

Eventually provide a way to export:

- Transactions
- Categories
- Budget data
- Debt data
- Debt balance history
- Recurring bills
- Subscriptions

CSV is sufficient for most exports.

---

# 32. Backup and restore

Eventually I want a simple backup process.

Since this is a local SQLite application, this may be as simple as safely backing up:

- SQLite database
- Application configuration

Potentially provide:

- Create backup
- Restore backup

Do not build a complex backup system unless necessary.

---

# 33. Privacy and local-only design

This application contains sensitive personal financial information.

For the initial version:

- Keep all data local.
- Do not require cloud services.
- Do not require an external database.
- Do not send transaction data to third-party APIs.
- Do not integrate Plaid or similar services.
- Do not add telemetry.
- Do not add analytics tracking.
- Do not require user registration.
- Do not require authentication unless there is a strong local-security reason we explicitly decide to add later.

If AI-assisted categorization or statement parsing is ever added, it must be optional and designed with privacy in mind.

---

# 34. Things I specifically do NOT need initially

Avoid scope creep.

Version 1 does NOT need:

- Bank account syncing
- Plaid integration
- Multiple users
- SaaS architecture
- Cloud hosting
- Mobile applications
- Investment portfolio management
- Tax preparation
- Credit score monitoring
- Machine-learning categorization
- Real-time banking APIs
- Full double-entry accounting
- Complex permissions
- Kubernetes
- Production Docker stacks; Docker Compose is approved for local development only
- Microservices
- Redis
- Celery
- Message queues
- GraphQL
- Complex caching

This is a local personal finance application.

Keep it appropriately simple.

---

# 35. Testing priorities

I do not need exhaustive enterprise-grade test coverage.

I do want good automated tests around important financial logic.

Prioritize tests for:

- Duplicate transaction detection
- Categorization rules
- Needs/Wants/Savings totals
- Remaining / Unallocated calculation
- Credit card payment exclusion
- Internal transfer handling
- Savings transfer handling
- Refunds
- Monthly aggregation
- Yearly aggregation
- Historical budget goals

Financial calculations should be trustworthy.

Frontend snapshot testing is much less important than testing the calculations.

---

# 36. Money handling

Do not use floating-point numbers for currency calculations.

Use appropriate decimal-based handling in both Django and frontend logic where necessary.

In Django, financial model fields should normally use `DecimalField`.

Be mindful of:

- Positive vs negative transaction representations
- Rounding
- Refunds
- Credits

Choose a consistent internal convention and document it.

---

# 37. Suggested domain models

Please evaluate and refine a model structure roughly resembling:

- Account
- Transaction
- ImportBatch
- ImportProfile
- BudgetCategory
- Subcategory
- CategorizationRule
- MonthlyBudget
- MonthlyBudgetItem
- Debt
- DebtBalanceSnapshot
- StudentLoan
- RecurringBill
- Subscription

You do not have to use exactly these models.

Recommend the simplest structure that supports the requirements without creating giant models or excessive abstraction.

---

# 38. Suggested MVP development order

Please critique this ordering before we begin.

My tentative development sequence is:

### Phase 1 — Foundation

1. Django project
2. React/Vite project
3. Django REST Framework
4. SQLite
5. Basic navigation and frontend layout

### Phase 2 — Core financial model

6. Accounts
7. Budget categories
8. Subcategories
9. Transactions
10. Import batches

### Phase 3 — Transaction import

11. CSV import
12. Column mapping/import profiles
13. Duplicate detection
14. Transaction list

### Phase 4 — Categorization workflow

15. Manual categorization
16. Merchant cleanup
17. Categorization rules
18. Review Queue
19. Bulk categorization

### Phase 5 — Monthly analysis

20. Income calculation
21. Needs/Wants/Savings calculation
22. Remaining / Unallocated calculation
23. Monthly dashboard
24. Allocation chart
25. Subcategory breakdown
26. Monthly budget goals

### Phase 6 — Historical reporting

27. Monthly history
28. Yearly dashboard
29. Month-to-month comparisons
30. Spending trend charts

### Phase 7 — Recurring expenses

31. Monthly bills
32. Monthly subscriptions
33. Annual subscriptions
34. Recurring-cost summary

### Phase 8 — Debt

35. Debt records
36. Debt balance snapshots
37. Student loan grouping
38. Debt charts

### Phase 9 — Quality-of-life improvements

39. Export
40. Backup/restore
41. Improved import profiles
42. Automatic bill/subscription matching
43. Credit card statement import

Do not implement all phases at once.

Finish and test one coherent milestone before moving to the next.

---

# 39. Initial response I want from you

Before writing application code, respond with the following.

## A. Product assessment

Give me your assessment of the idea.

Tell me:

- What parts are particularly useful
- What parts may be unnecessary
- What you would change
- Where you see potential complexity
- Where there may be hidden edge cases

## B. Proposed architecture

Recommend a simple architecture.

Include:

- Django apps
- Major Django models
- REST API organization
- React directory/component structure
- State/data fetching strategy
- Styling approach
- Charting library
- SQLite considerations

Do not propose enterprise infrastructure.

## C. Proposed data model

Sketch the important entities and relationships.

Explain why each entity exists.

Pay particular attention to:

- Transactions
- Accounts
- Transfers
- Savings
- Categories
- Rules
- Budgets
- Debt history
- Recurring bills

## D. Transaction accounting model

This deserves special attention.

Propose the simplest model that correctly handles:

- Purchases
- Income
- Refunds
- Credit card payments
- Internal transfers
- Savings transfers
- Loan payments
- Excluded transactions

Explain how each transaction type affects:

- Income
- Needs
- Wants
- Savings
- Remaining / Unallocated

Use several concrete examples.

## E. Import workflow

Explain how:

`CSV → Import Batch → duplicate detection → categorization rules → Review Queue`

should work.

## F. Questions for me

Ask me the important product questions that genuinely require my preference.

Group related questions together rather than asking dozens of small questions individually.

Important topics may include:

- Bank CSV formats
- How I want transfers handled
- Whether investment contributions count as Savings
- Account balance tracking
- Budget rollover behavior
- How monthly income should be recognized
- Whether refunds should reduce the original subcategory
- Whether Savings goals need separate named goals
- Debt behavior
- Historical spreadsheet import
- Dashboard preferences

Do not ask me questions for decisions where you can recommend a sensible default instead.

## G. Recommended improvements

Suggest useful improvements I have not considered.

Prioritize things that:

- Reduce repetitive monthly work
- Improve accuracy
- Make trends easier to understand
- Make financial behavior more actionable

Avoid suggesting features that turn this into an enormous finance platform.

## H. Revised MVP roadmap

Review my proposed development phases and recommend any changes.

Explain why.

---

# 40. Confirmed MVP decisions

The following decisions refine and take precedence over earlier tentative options in this specification:

- **Local development with Docker Compose:** Docker Compose is the recommended local development workflow, using the exact filename `docker-compose.yaml`. Run only the Django/DRF backend and React/Vite frontend application services, keep SQLite, and use source bind mounts for reloads. The stack is local-development only; no production orchestration or extra infrastructure services.
- **Stable budget buckets:** Needs, Wants, and Savings are fixed domain values (`NEEDS`, `WANTS`, `SAVINGS`), not user-managed top-level categories. Users can edit, archive, and move subcategories. Allocation targets are configurable and snapshotted for each month when it becomes relevant.
- **Initial CSV support:** Start with one real CSV format supplied by the user. Do not build a generalized importer or plugin system until another real format requires it.
- **Filename and account context:** Preserve the original filename on each Import Batch for audit purposes only. Transaction dates come from CSV contents; account identity comes from the account selected for the import. Filenames are never authoritative financial data.
- **Duplicate handling:** Automatically reject only high-confidence duplicates. Transactions that merely resemble existing ones based on date, amount, or description go to review.
- **Savings transfers:** Transfers into a designated savings-destination account qualify as Savings by default, with a per-transaction override. When both sides are imported, count the transfer once using the source-side posted date.
- **Refunds:** Link refunds to original purchases where possible and offset the original subcategory in the refund month. Unmatched refunds require review; they are not automatically income.
- **Debt payments:** Required loan payments may be Needs in the MVP. The model must leave room for intentional extra debt paydown to count toward Savings or another financial goal later; principal/interest parsing is out of MVP scope.
- **Balances and historical data:** Do not calculate or prominently display account balances in the MVP. Historical spreadsheet migration is deferred until the new-data workflow works.
- **Monthly budgets:** No rollover. Snapshot the current allocation targets and applicable subcategory goals when a month becomes relevant; do not generate future monthly budget records in advance.
- **Income date:** Use posted date when available, otherwise transaction date.
- **Dashboard:** Use target-versus-actual bars as the primary allocation visualization. Any later donut chart must be clearly labeled and keep Remaining / Unallocated separate.
- **Delivery scope:** Build and verify the import → categorization → review → monthly reporting workflow before prioritizing debt, recurring expenses, subscriptions, statement parsing, or other later features.
- **Transaction classification and review are separate:** Classification values are `UNKNOWN`, `EXPENSE`, `INCOME`, `REFUND`, `TRANSFER`, and `EXCLUDED`. A transaction may be an `EXPENSE` without a subcategory. Any later review/categorization state must be stored separately from financial classification.
- **Historical bucket snapshot:** A categorized transaction stores the applicable `NEEDS`, `WANTS`, or `SAVINGS` bucket as a snapshot. Assigning a subcategory defaults the snapshot from its current bucket; changing a subcategory's bucket does not rewrite existing transactions. An explicit recategorization may update the transaction's stored bucket.
- **Transaction amounts:** Store amounts from the account's perspective: incoming funds are positive and outgoing funds are negative. Keep the imported raw description and amount intact.
- **Milestone 2 CSV import:** The first importer supports the observed SoFi checking CSV structure (`Date`, `Description`, `Type`, `Amount`, `Current balance`, `Status`) without treating its filename as financial input. `Current balance` is ignored; source type is preserved; only unambiguous debit-card outflows and earned-interest inflows are classified automatically. Other types remain `UNKNOWN`.
- **Import safety:** Preview and confirmation use a 20 MiB maximum. Require strict ISO dates, Decimal amounts with at most two fractional digits, and Posted status. Any malformed/non-Posted row blocks the whole batch; an otherwise valid row with ambiguous classification can be imported as `UNKNOWN`.
- **Import audit and duplicate protection:** Each successful import creates an ImportBatch with selected account, original filename, parser identifier, timestamp, source/imported row counts, and SHA-256 file hash. Block exact-content re-imports per account; do not silently deduplicate transaction lookalikes. Link imported transactions to batch and 1-based source data row. CSV contents are transient and are not stored.
- **Local financial-file privacy:** Root-level CSV exports are ignored by Git and excluded from Docker build contexts. Sanitized CSV fixtures under backend tests remain available to native and container builds.
- **Arbitrary import ranges:** A CSV may cover any range, including multiple months or years. ImportBatch is provenance, not a reporting period; transaction dates are authoritative, and filenames never determine dates or account identity.
- **Import date-range audit:** Each successful ImportBatch stores earliest and latest transaction dates from its parsed rows. Import history exposes these dates without deriving them from filenames.
- **Institution boundary:** The first concrete parser is specific to the supplied SoFi checking format. Source-specific headers, status interpretation, and classification hints stay in that parser; normalized transactions, ImportBatch, API resources, and future reports remain institution-independent. Add a second small parser only after inspecting a real second format.
- **Overlapping imports:** Exact-file SHA-256 protection is supplemented by account-scoped fingerprint and multiplicity matching. Preserve batch, source row, source type, date, description, amount, and file hash as provenance; do not silently remove similar transactions.
- **Reporting periods and analysis:** Reporting is based on transaction dates, never import-file or ImportBatch boundaries. The planned hierarchy is All Time → Year → Month → Transactions, with totals, subcategory breakdowns, monthly/yearly comparisons, trends over time, and auditable drill-downs. Custom date ranges should be straightforward later but are not required before All Time, Year, and Month.
- **Savings and historical reporting:** All reporting must use each transaction's snapshotted Needs/Wants/Savings bucket. Savings remains actual qualifying activity; Remaining / Unallocated is income minus Needs, Wants, and Savings, never silently reclassified as Savings.
- **Overlapping imports:** Use account-scoped multiplicity matching. The current fingerprint uses transaction date, posted date when available, signed Decimal amount quantized to cents, source type trimmed and case-folded, and raw description trimmed, whitespace-collapsed, and case-folded. Account is the matching scope; merchant, filename, batch, and source row are not fingerprint fields. A future stable source transaction ID may provide stronger identity.
- **Overlap terminology and behavior:** Fingerprint/multiplicity matches are called `already represented`, not certain duplicates. For each fingerprint, match up to the number of existing non-confirmed-duplicate occurrences; extra incoming occurrences are `possible duplicate` only if an existing candidate has the same account, date, and amount plus an equal normalized raw description or source type, otherwise they are new. Repeated identical rows in one new file are all preserved as new if no earlier occurrence exists. Possible duplicates are imported into Review. Preview shows each row's outcome before confirmation; confirm recomputes overlap atomically.
- **Possible-duplicate context and resolution:** Preserve candidate transaction relationships so Review can show their account, date, raw description/merchant, amount, and source type. Duplicate resolution is separate from classification and review status: unresolved, distinct, or confirmed duplicate. Confirmed duplicates remain stored and auditable, reference the selected candidate, and are excluded from future financial totals without changing classification to `EXCLUDED`.
- **Review completion:** Review status is `PENDING`, `REVIEWED`, or `NOT_REQUIRED`; it is not transaction classification. Most review reasons derive from the transaction state, while unresolved possible-duplicate state is explicit. UNKNOWN, uncategorized EXPENSE/REFUND, and unresolved possible duplicates cannot be marked reviewed. EXPENSE and REFUND require an active Needs/Wants subcategory when newly assigned and a valid historical bucket snapshot. INCOME and EXCLUDED need no subcategory; TRANSFER may be reviewed without one and may retain a Savings subcategory without implying qualifying savings activity. A later edit that makes a transaction unresolved returns it to PENDING.
- **Category consistency:** Explicitly changing classification clears both subcategory and bucket snapshot when the old subcategory is invalid for the new classification. Assigning a valid subcategory snapshots its current bucket. INCOME, UNKNOWN, and EXCLUDED do not accept subcategories; EXPENSE/REFUND accept Needs/Wants; TRANSFER accepts Savings.
- **Future reporting exclusions:** Future financial totals must omit `EXCLUDED`, unresolved possible duplicates, and confirmed duplicates. A possible duplicate resolved as DISTINCT may contribute once its normal review requirements are satisfied.
- **Milestone 3 scope:** Manual classification/category/merchant/notes edits, a Review Queue, and atomic bulk classification/category/review actions are included. Do not add automatic categorization or merchant rules, refund linking, transfer pairing, savings calculations, reporting dashboards, debt, recurring bills, or subscriptions as part of this milestone.
- **Frontend mutation convention:** Every mutation, including imports, transaction edits, bulk actions, review completion, and duplicate resolution, must use the shared CSRF-aware `apiFetch` in `frontend/src/api.ts`.
- **Normal category management:** PennyTracker's Categories screen is the normal interface for creating, renaming, moving, archiving, and reactivating Subcategories. Django admin remains an administrative/development tool, not a required user workflow.
- **Subcategory name uniqueness:** Prevent duplicate names using exact case-insensitive matching. A matching archived Subcategory must be reactivated rather than copied; do not add fuzzy matching.
- **Archived Subcategories:** Keep archived Subcategories available for historical display and existing transaction links, but exclude them from new assignments. Reactivation makes the Subcategory available for future assignments.
- **Category bucket changes:** Moving a Subcategory between Needs, Wants, and Savings affects future assignments only. Existing Transactions keep their stored budget-bucket snapshots unless explicitly recategorized.
- **Atomic inline creation:** Review may create and assign a Subcategory in one API action. Create the category, validate transaction eligibility through the existing transaction serializer/model behavior, assign it, and snapshot the bucket within one database transaction; failures must roll back both changes.
- **Theme and visual direction:** Support Light, Dark, and System themes with browser localStorage persistence and synchronous initialization before React renders. Use CSS custom properties, an accessible compact theme control, and a dense, calm personal-finance ledger aesthetic rather than a generic SaaS dashboard.
- **Milestone 3.5 scope:** Categories management, inline create-and-assign, compact Transactions/Review editing, restrained ledger styling, and themes are included. Automatic categorization rules, reporting, and all later product features remain deferred.
- **Dark palette:** Dark and System-dark themes use neutral charcoal/slate backgrounds, surfaces, borders, inputs, and text; PennyTracker green is reserved for intentional brand, focus, selection, primary action, and positive-money emphasis.
- **Ledger query behavior:** Transactions and Review use the same API-backed filters and sortable columns with stable server-side ordering and pagination. Common query parameters are `start_date`, `end_date`, `search`, `account`, `source_type`, `classification`, `subcategory` (ID or `uncategorized`), `budget_bucket`, and `review_status`; Review may also use `review_reason`. `ordering` accepts `date`, `description`, `account`, `source_type`, `classification`, `subcategory`, `review_status`, and `amount`, prefixed by `-` for descending. Do not fetch the entire transaction history to sort or filter in the browser.
- **Owned-account movement:** Movement between accounts owned by the user is `TRANSFER`, not Income or ordinary spending, in either direction. A manually classified Transfer may be reviewed without a Subcategory; an optional Savings Subcategory does not by itself establish qualifying Savings activity.
- **Ambiguous deposits:** A generic source type such as `DEPOSIT` is not evidence of Income. Keep it `UNKNOWN` unless the user or a transparent categorization rule resolves it.
- **Future transfer reporting:** Savings contributions and withdrawals may later be derived from transfer direction rather than separate classifications. When both sides of one internal transfer are imported, future matching should recognize the pair as one economic transfer and avoid double-counting. Transfer pairing and Savings calculations are not part of Milestone 3.6.
- **Milestone 3.6 scope:** Neutralize dark-mode surfaces, add API-backed shared sorting/filtering and pagination to Transactions and Review, and lock in the owned-account transfer/ambiguous-deposit rules. Do not add categorization rules, transfer pairing, savings calculations, or reporting.

# Final development philosophy

The spreadsheet already works.

The purpose of this application is not merely to recreate the spreadsheet with React.

The application should improve upon the spreadsheet by making the process:

- Faster
- Easier to maintain
- Easier to audit
- Easier to explore
- More automated
- More visually understandable

The most important user experience is:

**Import my transactions, review the handful that need attention, and immediately understand where my money went.**

Build around that experience.
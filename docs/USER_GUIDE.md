# User Guide

PennyTracker is designed for importing transaction history, resolving what imported transactions mean, and then reviewing the resulting reports. You do not need to type each purchase or create a category rule for every transaction.

## Step 1 — Configure your accounts

An **Account** is the local ledger container for transactions from one account, such as `Example Checking`, `Example Savings`, or `Example Credit Card`. PennyTracker currently creates and edits Accounts in Django Admin, not in the web app. Follow [first-run setup](SETUP.md#create-the-first-admin-user-and-account) if you have not created an admin login yet.

In Admin, create an Account with a name, optionally enter an Institution label, choose its account type, and leave **Is active** enabled if you want to import to it. Account types include Checking, Savings, Credit card, Loan, Investment, and Other. The institution name is only descriptive; it does not connect to a bank.

**Is savings destination** is account metadata. It does not infer a Savings contribution or withdrawal. To include Savings activity in reports, classify the relevant transaction as a Transfer and explicitly assign a Savings effect and Savings Subcategory in Review.

## Step 2 — Export a supported CSV

1. Sign in directly to your financial institution's website or app.
2. Export or download transaction history as a CSV file.
3. Import that file into PennyTracker yourself.

PennyTracker does not ask for bank credentials and does not connect to financial institutions. Its current built-in parser recognizes a **SoFi checking CSV** with these required columns: `Date`, `Description`, `Type`, `Amount`, and `Status`. Dates must use `YYYY-MM-DD`; amounts must be plain decimal values with up to two fractional digits; only rows with `Posted` status are accepted. Files must be UTF-8 encoded and no larger than 20 MiB.

This is not a general-purpose CSV mapper: other providers' formats, including arbitrary card exports, may not be recognized. An unsupported structure or invalid row prevents that file from being imported. There is no bank API or automatic download to fall back on.

The signed `Amount` is retained from the CSV. For the recognized format, a negative `DEBIT_CARD` row starts as an Expense and a positive `INTEREST_EARNED` row starts as Income. Other source types start as `UNKNOWN` unless an eligible Rule resolves them.

## Step 3 — Import the CSV

In **Transactions**, choose **Import CSV**:

1. Select the destination Account.
2. Choose the CSV file and select **Preview**.
3. Check the row counts and preview results, including new rows, rows already represented, possible duplicates, invalid rows, and rows that need Review.
4. If the file is valid, choose **Confirm import**.

Preview lets you check the selected Account, parsed source data, and categorization results before writing transactions. Invalid rows block confirmation for the file; fix the CSV and preview it again. The original source description is preserved for auditing, and the original filename is kept as import metadata.

Importing the exact same file to the same Account is blocked. With overlapping but non-identical exports, rows that match existing transactions can be marked **already represented** and skipped; ambiguous matches are retained as possible duplicates for Review instead of being silently discarded. Review possible duplicates before relying on report totals.

Some rows may be automatically resolved by a matching Rule. Others remain unknown or require Review; import does not mean every row has already been categorized.

## Step 4 — Review transactions

A transaction can be present in the ledger without PennyTracker knowing what it means financially. Open **Review** to see transactions that need attention. Resolve the financial outcome, choose a required Subcategory where applicable, and save. A resolved transaction is marked reviewed when no other blocker remains. An unresolved possible duplicate or rule conflict, for example, can still require attention.

The **Classification** describes what happened:

| Classification | Meaning |
|---|---|
| `UNKNOWN` | The financial meaning is not known yet. |
| `EXPENSE` | A purchase or other spending. Assign a Needs or Wants Subcategory. |
| `INCOME` | Ordinary income. |
| `REFUND` | Money returned for a purchase; assign the related Needs or Wants Subcategory so it can offset that bucket. |
| `TRANSFER` | Movement between accounts or another transfer. An ordinary Transfer is not automatically spending, Income, or Savings. |
| `OTHER_INFLOW` | An inflow kept separate from ordinary Income; an Other Inflows Subcategory is optional. |
| `EXCLUDED` | A transaction intentionally excluded from budget reporting. |

For a **Savings Contribution** or **Savings Withdrawal**, choose `TRANSFER`, select the Savings effect, and choose a Savings Subcategory. An ordinary Transfer has no Savings effect. Account type, transfer direction, or an account's savings-destination setting does not decide this for you.

Transfers that do not match a deliberate categorization Rule need Review so you can confirm whether they are ordinary transfers or explicitly designated Savings activity.

At a high level, Review may be needed because the financial meaning is unknown, an Expense or Refund lacks a required category, a possible duplicate is unresolved, or matching Rules conflict. The Review page also allows possible duplicates to be resolved as distinct transactions or confirmed duplicates.

Saving a deliberate financial resolution marks a transaction reviewed when no blockers remain. Merely editing its notes does not resolve a financial question.

## Step 5 — Manage Categories and Subcategories

Classification and Subcategory are different:

- **Classification**: what financially happened (Expense, Income, Transfer, and so on).
- **Subcategory**: what kind of eligible activity it was, grouped under Needs, Wants, Savings, or Other Inflows.

Examples:

- `EXPENSE → Wants → Fast Food`
- `EXPENSE → Needs → Groceries`
- `TRANSFER → Savings Contribution → Emergency Fund`

Open **Categories** to create, rename, move between budget buckets, archive, or reactivate Subcategories. The fresh database has no default Subcategories. Active budget Subcategories belong to Needs, Wants, or Savings. Other Inflows Subcategories are a separate optional group.

Archived Subcategories remain visible for historical transactions but are unavailable for new assignments. Moving a Subcategory affects future assignments; transactions keep the budget bucket recorded when they were categorized.

## Step 6 — Teach PennyTracker with Rules

When resolving a transaction in Review, you can optionally select **Remember this outcome for future matching transactions**. PennyTracker does not invent a Rule without that explicit choice. You can edit Rules in **Rules**.

Rules match the transaction's **raw description**, case-insensitively, using either **contains** or **starts with**. They are deterministic and visible; they do not guess from a merchant name or use fuzzy matching. For example, with synthetic descriptions, you could choose outcomes like:

- `SYNTHETIC PAYROLL` → Income
- `SYNTHETIC CAFE` → Expense → Wants → Fast Food
- `AUTO SAVE` → Transfer → Savings Contribution → Emergency Fund

Choose a match pattern specific enough for the intended outcome. If eligible matching Rules produce conflicting outcomes, the transaction is sent to Review rather than being silently assigned. A clean matching Rule can automatically resolve future eligible transactions during import. When you create a Rule, PennyTracker may also offer to apply it to matching pending transactions; you choose whether to do that.

## Step 7 — Understand Reports

Open **Reports** to view **All Time** and navigate into a calendar **Year** and **Month**. The range controls also offer **YTD**, **Last 30 days**, **Last 7 days**, and an inclusive **Custom** date range.

Report periods use each transaction's date; posted dates, import batches, and filenames do not determine the reporting period. Custom date ranges include both the From and To dates.

The accounting summary separates:

- **Income**: ordinary reported Income.
- **Other Inflows**: separate inflows that are not added to Income or its allocation percentages.
- **Needs** and **Wants**: eligible categorized Expenses and Refunds, netted within the assigned bucket.
- **Savings Contributions** and **Savings Withdrawals**: separately reported, explicitly designated Savings transfers.
- **Net Savings Activity**: Contributions minus Withdrawals.
- **Remaining / Unallocated**: Income minus Needs, Wants, and Savings Contributions. Savings Withdrawals do not increase Remaining; Other Inflows stay outside this formula.

Allocation targets are configurable guideposts. The report shows percentages against Income when that calculation is available. With positive Income and non-negative allocations, the donut includes Needs, Wants, Savings Contributions, and Remaining. If those allocations exceed Income, it shows their shares of allocations and flags the excess. Negative allocations or non-positive Income make the donut unavailable. Incomplete data is called out so unresolved transactions are not mistaken for a complete picture.

Select a summary line or Subcategory to inspect its contributing transactions. All Time and Year views show monthly trends; custom-range trends are daily for ranges up to 30 days and monthly for longer ranges. Partial months in a custom range include only selected dates, and range percentages use range totals rather than averages of period percentages. The drilldowns make report totals auditable.

## Step 8 — The recurring workflow

1. Export updated CSV history from a supported provider.
2. Import each file into the matching PennyTracker Account.
3. Review unknown transactions, category gaps, conflicts, and possible duplicates.
4. Optionally remember recurring, deterministic outcomes as Rules.
5. Check Reports and drill into any totals you want to audit.

As you deliberately add useful Rules, eligible repeat transactions can be resolved during import, reducing repetitive Review. PennyTracker still relies on you to export and import the CSVs.

import { useEffect, useState } from 'react'
import { apiFetch } from './api'
import TransactionTable from './TransactionTable'
import Categories from './Categories'
import Rules from './Rules'
import Reports from './Reports'
import './App.css'

type Account = { id: number; name: string; is_active: boolean }
type Classification = 'UNKNOWN' | 'EXPENSE' | 'INCOME' | 'REFUND' | 'TRANSFER' | 'EXCLUDED' | 'OTHER_INFLOW'
type Preview = {
  account: { id: number; name: string }
  filename: string
  source_row_count: number
  valid_row_count: number
  invalid_row_count: number
  new_row_count: number
  already_represented_row_count: number
  possible_duplicate_row_count: number
  review_required_row_count: number
  valid_rows: {
    source_row: number
    date: string
    posted_date: string | null
    raw_description: string
    amount: string
    source_type: string
    classification: Classification
    overlap_status: 'NEW' | 'ALREADY_REPRESENTED' | 'POSSIBLE_DUPLICATE'
    overlap_reason: string
    categorization: {
      status: 'AUTO_RESOLVED' | 'RULE_CONFLICT' | 'NOT_CATEGORIZED'
      classification: Classification | null
      savings_effect: 'NONE' | 'CONTRIBUTION' | 'WITHDRAWAL'
      subcategory: { id: number; name: string; budget_bucket: 'NEEDS' | 'WANTS' | 'SAVINGS' | null } | null
      rules: {
        id: number
        classification: Classification
        match_type: 'CONTAINS' | 'STARTS_WITH'
        match_value: string
        savings_effect: 'NONE' | 'CONTRIBUTION' | 'WITHDRAWAL'
        subcategory_name: string | null
      }[]
    }
    duplicate_candidates: {
      id: number
      date: string
      raw_description: string
      merchant: string
      amount: string
      source_type: string
      account: string
    }[]
  }[]
  invalid_rows: { source_row: number; errors: string[] }[]
  already_imported: boolean
  can_import: boolean
}
type ImportResult = {
  filename: string
  account: string
  source_row_count: number
  imported_row_count: number
  already_represented_row_count: number
  possible_duplicate_row_count: number
}
type Theme = 'light' | 'dark' | 'system'

const classificationLabels: Record<Classification, string> = {
  UNKNOWN: 'Unknown',
  EXPENSE: 'Expense',
  INCOME: 'Income',
  REFUND: 'Refund',
  TRANSFER: 'Transfer',
  EXCLUDED: 'Excluded',
  OTHER_INFLOW: 'Other inflow',
}

function formatAmount(amount: string) {
  const negative = amount.startsWith('-')
  const [whole, fraction = '00'] = (negative ? amount.slice(1) : amount).split('.')
  const groupedWhole = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  return `${negative ? '-' : ''}$${groupedWhole}.${fraction.padEnd(2, '0')}`
}

function apiErrorMessage(result: unknown, status: number): string {
  const data = (result ?? {}) as Record<string, unknown>
  if (typeof data.detail === 'string') return data.detail
  const first = Object.entries(data).find(([, value]) => Array.isArray(value) && typeof value[0] === 'string')
  if (first) {
    const [field, messages] = first as [string, string[]]
    return field === 'non_field_errors' ? messages[0] : `${field[0].toUpperCase()}${field.slice(1).replace(/_/g, ' ')}: ${messages[0]}`
  }
  return `The import request failed (${status}). Please try again.`
}

type PreviewOutcome = {
  classification: Classification | null
  savings_effect: 'NONE' | 'CONTRIBUTION' | 'WITHDRAWAL'
  subcategory?: { name: string; budget_bucket: 'NEEDS' | 'WANTS' | 'SAVINGS' | null } | null
  subcategory_name?: string | null
}

function ruleText(rule: { id: number; match_type: string; match_value: string }) {
  return `#${rule.id} ${rule.match_type === 'STARTS_WITH' ? 'starts with' : 'contains'} “${rule.match_value}”`
}

function outcomeText(outcome: PreviewOutcome) {
  const name = outcome.subcategory?.name ?? outcome.subcategory_name
  const bucket = outcome.subcategory?.budget_bucket
  return [
    outcome.classification ? classificationLabels[outcome.classification] : 'Unspecified',
    outcome.savings_effect !== 'NONE' ? `Savings ${outcome.savings_effect.toLowerCase()}` : '',
    name ? `${name}${bucket ? ` (${bucket.charAt(0)}${bucket.slice(1).toLowerCase()})` : ''}` : '',
  ].filter(Boolean).join(' · ')
}

function App() {
  const [reload, setReload] = useState(0)
  const [reviewReason, setReviewReason] = useState('')
  const [reviewDates, setReviewDates] = useState({ start: '', end: '' })
  const [reportsVisited, setReportsVisited] = useState(false)
  const [view, setView] = useState<'transactions' | 'review' | 'categories' | 'rules' | 'reports'>('transactions')
  const [theme, setTheme] = useState<Theme>(() => {
    const saved = localStorage.getItem('pennytracker-theme')
    return saved === 'light' || saved === 'dark' || saved === 'system' ? saved : 'system'
  })
  const [showImport, setShowImport] = useState(false)
  const [accounts, setAccounts] = useState<Account[]>([])
  const [accountId, setAccountId] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [importResult, setImportResult] = useState<ImportResult | null>(null)
  const [importError, setImportError] = useState('')
  const [importing, setImporting] = useState(false)

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    localStorage.setItem('pennytracker-theme', theme)
  }, [theme])

  useEffect(() => {
    if (!showImport) return
    apiFetch('/api/accounts/')
      .then(async (response) => {
        if (!response.ok) throw new Error(`Request failed (${response.status})`)
        const result: Account[] = await response.json()
        setAccounts(result.filter((account) => account.is_active))
      })
      .catch((loadError) => {
        setImportError(loadError instanceof Error ? loadError.message : 'Unable to load accounts')
      })
  }, [showImport])

  function closeImport() {
    setShowImport(false)
    setPreview(null)
    setFile(null)
    setAccountId('')
    setImportError('')
  }

  async function submitImport(action: 'preview' | 'confirm') {
    if (!file || !accountId) return
    setImportError('')
    setImportResult(null)
    setImporting(true)
    const formData = new FormData()
    formData.append('account', accountId)
    formData.append('file', file)
    try {
      const response = await apiFetch(`/api/imports/${action === 'preview' ? 'preview/' : ''}`, {
        method: 'POST',
        body: formData,
      })
      const result = await response.json()
      if (!response.ok) throw new Error(apiErrorMessage(result, response.status))
      if (action === 'preview') {
        setPreview(result as Preview)
      } else {
        setImportResult(result as ImportResult)
        closeImport()
        setReload((current) => current + 1)
      }
    } catch (requestError) {
      setImportError(requestError instanceof Error ? requestError.message : 'Import request failed')
    } finally {
      setImporting(false)
    }
  }

  const previewHint = importing
    ? 'Preview is being generated.'
    : !accountId && !file
      ? 'Select an account and choose a CSV file to continue.'
      : !accountId
        ? 'Select an account to preview this import.'
        : !file
          ? 'Choose a CSV file to preview this import.'
          : ''
  const confirmHint = !preview || importing || preview.can_import
    ? ''
    : preview.already_imported
      ? 'This exact file was already imported to this account, so it cannot be imported again.'
      : 'Confirm is unavailable because the file has invalid rows. Fix the CSV and preview it again.'

  const previewing = view === 'transactions' && preview !== null

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a className="brand" href="/" aria-label="PennyTracker home">
          <span className="brand-mark" aria-hidden="true">$</span>
          <span>PennyTracker</span>
        </a>
        <nav aria-label="Main navigation">
          <button
            className={`nav-item ${view === 'transactions' ? 'nav-item--active' : ''}`}
            type="button"
            aria-current={view === 'transactions' ? 'page' : undefined}
            onClick={() => setView('transactions')}
          >
            Transactions
          </button>
          <button
            className={`nav-item ${view === 'review' ? 'nav-item--active' : ''}`}
            type="button"
            aria-current={view === 'review' ? 'page' : undefined}
            onClick={() => { setReviewReason(''); setReviewDates({ start: '', end: '' }); setView('review') }}
          >
            Review
          </button>
          <button
            className={`nav-item ${view === 'categories' ? 'nav-item--active' : ''}`}
            type="button"
            aria-current={view === 'categories' ? 'page' : undefined}
            onClick={() => setView('categories')}
          >
            Categories
          </button>
          <button
            className={`nav-item ${view === 'rules' ? 'nav-item--active' : ''}`}
            type="button"
            aria-current={view === 'rules' ? 'page' : undefined}
            onClick={() => setView('rules')}
          >
            Rules
          </button>
          <button
            className={`nav-item ${view === 'reports' ? 'nav-item--active' : ''}`}
            type="button"
            aria-current={view === 'reports' ? 'page' : undefined}
            onClick={() => {
              if (view !== 'reports') setReload((current) => current + 1)
              setReportsVisited(true)
              setView('reports')
            }}
          >
            Reports
          </button>
        </nav>
        <div className="sidebar-footer">
          <label className="theme-control">
            Theme
            <select aria-label="Theme" value={theme} onChange={(event) => setTheme(event.target.value as Theme)}>
              <option value="light">Light</option>
              <option value="dark">Dark</option>
              <option value="system">System</option>
            </select>
          </label>
          <p className="local-note">Your finances stay on this device.</p>
        </div>
      </aside>

      <main className="main-content">
        <header className="page-header">
          <div>
            <h1>{previewing ? 'Import preview' : view === 'review' ? 'Review' : view === 'categories' ? 'Categories' : view === 'rules' ? 'Rules' : view === 'reports' ? 'Reports' : 'Transactions'}</h1>
            <p className="page-description">
              {previewing
                ? 'Review the rows below, then confirm or close without importing.'
                : view === 'review'
                ? 'Resolve unknown classifications, uncategorized activity, and possible duplicates.'
                : view === 'categories'
                  ? 'Manage the subcategories used to organize transactions.'
                  : view === 'rules'
                    ? 'Manage the deterministic rules used to classify and categorize transactions.'
                    : view === 'reports'
                      ? 'Explore All Time, yearly, and monthly accounting with trends and auditable transaction details.'
                    : 'A clear view of activity across your accounts.'}
            </p>
          </div>
          {view !== 'categories' && view !== 'rules' && (
            <div className="page-actions">
              {!previewing && (
                <button className="secondary-button" type="button" onClick={() => setReload((current) => current + 1)}>
                  Refresh
                </button>
              )}
              {previewing && (
                <button
                  className="primary-button"
                  type="button"
                  disabled={!preview.can_import || importing}
                  onClick={() => void submitImport('confirm')}
                >
                  {importing ? 'Importing…' : 'Confirm import'}
                </button>
              )}
              {view === 'transactions' && (
                <button
                  className={previewing ? 'secondary-button' : 'primary-button'}
                  type="button"
                  disabled={previewing && importing}
                  onClick={() => {
                    if (showImport) {
                      closeImport()
                    } else {
                      setShowImport(true)
                      setImportResult(null)
                      setImportError('')
                    }
                  }}
                >
                  {previewing ? 'Close import' : showImport ? 'Cancel import' : 'Import CSV'}
                </button>
              )}
            </div>
          )}
        </header>

        {importResult && !showImport && view === 'transactions' && (
          <p className="import-success" role="status">
            Imported {importResult.imported_row_count} new or possible-duplicate rows; skipped
            {' '}{importResult.already_represented_row_count} already represented rows from
            {' '}{importResult.filename} into {importResult.account}.
            {' '}<button type="button" className="link-button" onClick={() => setImportResult(null)}>Dismiss</button>
          </p>
        )}

        {showImport && view === 'transactions' && (
          <section className="import-card" aria-labelledby="import-heading">
            <div className="import-heading">
              <div>
                <h2 id="import-heading">Import transactions</h2>
                <p>Select the destination account; the filename is kept for reference only.</p>
              </div>
            </div>
            {!preview && (
            <div className="import-controls">
              <label>
                Account
                <select disabled={importing} value={accountId} onChange={(event) => {
                  setAccountId(event.target.value)
                  setPreview(null)
                  setImportResult(null)
                }}>
                  <option value="">Select an account</option>
                  {accounts.map((account) => (
                    <option key={account.id} value={account.id}>{account.name}</option>
                  ))}
                </select>
              </label>
              <label>
                CSV file (20 MiB maximum)
                <input
                  disabled={importing}
                  type="file"
                  accept=".csv,text/csv"
                  onChange={(event) => {
                    setFile(event.target.files?.[0] ?? null)
                    setPreview(null)
                    setImportResult(null)
                  }}
                />
              </label>
              <button
                className="primary-button"
                type="button"
                disabled={!accountId || !file || importing}
                onClick={() => void submitImport('preview')}
              >
                {importing ? 'Working…' : 'Preview'}
              </button>
            </div>
            )}

            {!preview && previewHint && !importError && <p className="import-hint">{previewHint}</p>}
            {confirmHint && <p className="import-hint" role="status">{confirmHint}</p>}
            {importError && <p className="message message--error" role="alert">{importError}</p>}
            {preview && (
              <div className="preview-content">
                <p className="preview-summary">
                  {preview.filename} · {preview.source_row_count} source rows · {preview.new_row_count} new ·
                  {' '}{preview.already_represented_row_count} already represented ·
                  {' '}{preview.possible_duplicate_row_count} possible duplicates ·
                  {' '}{preview.invalid_row_count} invalid ·
                  {' '}{preview.review_required_row_count} need review · Account: {preview.account.name}
                </p>
                {preview.already_imported && (
                  <p className="preview-warning" role="alert">This exact file was already imported to this account.</p>
                )}
                {preview.valid_rows.length > 0 && (
                  <div className="table-scroll">
                    <table>
                      <thead>
                        <tr>
                          <th scope="col">Row / date</th>
                          <th scope="col">Description</th>
                          <th scope="col">Source type</th>
                          <th scope="col">Classification</th>
                          <th scope="col">Import result</th>
                          <th scope="col" className="amount-cell">Amount</th>
                        </tr>
                      </thead>
                      <tbody>
                        {preview.valid_rows.map((row) => (
                          <tr key={row.source_row}>
                            <td>{row.source_row} · {row.date}</td>
                            <td>{row.raw_description}</td>
                            <td>{row.source_type}</td>
                            <td>
                              <span className={`classification classification--${row.classification.toLowerCase()}`}>
                                {classificationLabels[row.classification]}
                              </span>
                            </td>
                            <td>
                              <span className={`overlap-status overlap-status--${row.overlap_status.toLowerCase()}`}>
                                {row.overlap_status === 'ALREADY_REPRESENTED'
                                  ? 'Already represented'
                                  : row.overlap_status === 'POSSIBLE_DUPLICATE'
                                    ? 'Possible duplicate'
                                    : 'New'}
                              </span>
                              {row.duplicate_candidates.length > 0 && (
                                <small className="candidate-summary">
                                  {row.overlap_reason} Compared with {row.duplicate_candidates.map((candidate) =>
                                    `#${candidate.id} ${candidate.account} · ${candidate.date} · ${candidate.raw_description} · ${formatAmount(candidate.amount)} · ${candidate.source_type}`,
                                  ).join(' | ')}
                                </small>
                              )}
                              {row.categorization.status === 'AUTO_RESOLVED' && row.categorization.classification && (
                                <small className="categorization-preview">
                                  Auto-resolved: {outcomeText(row.categorization)}
                                  {' '}({row.categorization.rules.length} matching rule{row.categorization.rules.length === 1 ? '' : 's'}:
                                  {' '}{row.categorization.rules.map(ruleText).join('; ')})
                                </small>
                              )}
                              {row.categorization.status === 'RULE_CONFLICT' && (
                                <small className="categorization-conflict">
                                  Rule conflict — requires Review. Conflicting rules:
                                  {' '}{row.categorization.rules.map((rule) => `${ruleText(rule)} → ${outcomeText(rule)}`).join(' | ')}
                                </small>
                              )}
                            </td>
                            <td className={`amount-cell ${row.amount.startsWith('-') ? 'amount-negative' : 'amount-positive'}`}>
                              {formatAmount(row.amount)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                {preview.review_required_row_count > 0 && (
                  <p className="preview-warning">
                    Rows needing attention will be imported into Review; possible duplicates remain excluded from future totals until resolved.
                  </p>
                )}
                {preview.invalid_rows.length > 0 && (
                  <div className="invalid-rows">
                    <h3>Rows that prevent this import</h3>
                    {preview.invalid_rows.map((row) => (
                      <p key={row.source_row}>Row {row.source_row}: {row.errors.join(' ')}</p>
                    ))}
                    <p>No rows will be imported until every row is valid.</p>
                  </div>
                )}
              </div>
            )}
          </section>
        )}

        {reportsVisited && <div hidden={view !== 'reports'}>
          <Reports reload={reload} onReview={(reason, start, end) => {
            setReviewReason(reason)
            setReviewDates({ start, end })
            setView('review')
          }} />
        </div>}
        {view === 'reports'
          ? null
          : view === 'categories'
          ? <Categories />
          : view === 'rules'
            ? <Rules reload={reload} onChanged={() => setReload((current) => current + 1)} />
            : previewing
              ? null
              : <TransactionTable key={`${view}-${reviewReason}-${reviewDates.start}-${reviewDates.end}`} mode={view} reload={reload}
                  initialReason={view === 'review' ? reviewReason : ''}
                  initialStartDate={view === 'review' ? reviewDates.start : ''}
                  initialEndDate={view === 'review' ? reviewDates.end : ''} />}
      </main>
    </div>
  )
}

export default App

import { useEffect, useState } from 'react'
import { apiFetch } from './api'
import TransactionTable from './TransactionTable'
import './App.css'

type Account = { id: number; name: string; is_active: boolean }
type Classification = 'UNKNOWN' | 'EXPENSE' | 'INCOME' | 'REFUND' | 'TRANSFER' | 'EXCLUDED'
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

const classificationLabels: Record<Classification, string> = {
  UNKNOWN: 'Unknown',
  EXPENSE: 'Expense',
  INCOME: 'Income',
  REFUND: 'Refund',
  TRANSFER: 'Transfer',
  EXCLUDED: 'Excluded',
}

function formatAmount(amount: string) {
  const negative = amount.startsWith('-')
  const [whole, fraction = '00'] = (negative ? amount.slice(1) : amount).split('.')
  const groupedWhole = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  return `${negative ? '-' : ''}$${groupedWhole}.${fraction.padEnd(2, '0')}`
}

function App() {
  const [reload, setReload] = useState(0)
  const [view, setView] = useState<'transactions' | 'review'>('transactions')
  const [showImport, setShowImport] = useState(false)
  const [accounts, setAccounts] = useState<Account[]>([])
  const [accountId, setAccountId] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [importResult, setImportResult] = useState<ImportResult | null>(null)
  const [importError, setImportError] = useState('')
  const [importing, setImporting] = useState(false)

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
      if (!response.ok) throw new Error(result.detail || `Request failed (${response.status})`)
      if (action === 'preview') {
        setPreview(result as Preview)
      } else {
        setImportResult(result as ImportResult)
        setPreview(null)
        setReload((current) => current + 1)
      }
    } catch (requestError) {
      setImportError(requestError instanceof Error ? requestError.message : 'Import request failed')
    } finally {
      setImporting(false)
    }
  }

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
            <span className="nav-icon" aria-hidden="true">▤</span>
            Transactions
          </button>
          <button
            className={`nav-item ${view === 'review' ? 'nav-item--active' : ''}`}
            type="button"
            aria-current={view === 'review' ? 'page' : undefined}
            onClick={() => setView('review')}
          >
            <span className="nav-icon" aria-hidden="true">✓</span>
            Review
          </button>
        </nav>
        <p className="local-note">Your finances stay on this device.</p>
      </aside>

      <main className="main-content" id={view}>
        <header className="page-header">
          <div>
            <p className="eyebrow">{view === 'review' ? 'REVIEW QUEUE' : 'LEDGER'}</p>
            <h1>{view === 'review' ? 'Review' : 'Transactions'}</h1>
            <p className="page-description">
              {view === 'review'
                ? 'Resolve unknown classifications, uncategorized activity, and possible duplicates.'
                : 'A clear view of activity across your accounts.'}
            </p>
          </div>
          {view === 'transactions' && (
            <button
              className="primary-button"
              type="button"
              onClick={() => {
                setShowImport((shown) => !shown)
                setImportError('')
              }}
            >
              {showImport ? 'Close import' : 'Import CSV'}
            </button>
          )}
        </header>

        {showImport && view === 'transactions' && (
          <section className="import-card" aria-labelledby="import-heading">
            <div className="import-heading">
              <div>
                <h2 id="import-heading">Import transactions</h2>
                <p>Select the destination account; the filename is kept for reference only.</p>
              </div>
            </div>
            <div className="import-controls">
              <label>
                Account
                <select value={accountId} onChange={(event) => {
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

            {importError && <p className="message message--error" role="alert">{importError}</p>}
            {importResult && (
              <p className="import-success" role="status">
                Imported {importResult.imported_row_count} new or possible-duplicate rows; skipped
                {' '}{importResult.already_represented_row_count} already represented rows from
                {' '}{importResult.filename} into {importResult.account}.
              </p>
            )}
            {preview && (
              <div className="preview-content">
                <p className="preview-summary">
                  {preview.source_row_count} source rows · {preview.new_row_count} new ·
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
                <button
                  className="primary-button"
                  type="button"
                  disabled={!preview.can_import || importing}
                  onClick={() => void submitImport('confirm')}
                >
                  {importing ? 'Importing…' : 'Confirm import'}
                </button>
              </div>
            )}
          </section>
        )}

        <TransactionTable mode={view} reload={reload} />
      </main>
    </div>
  )
}

export default App

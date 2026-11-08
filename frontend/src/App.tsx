import { useEffect, useState } from 'react'
import { apiFetch } from './api'
import './App.css'

type Transaction = {
  id: number
  account_name: string
  subcategory_name: string | null
  date: string
  raw_description: string
  merchant: string
  amount: string
  classification: 'UNKNOWN' | 'EXPENSE' | 'INCOME' | 'REFUND' | 'TRANSFER' | 'EXCLUDED'
  budget_bucket: '' | 'NEEDS' | 'WANTS' | 'SAVINGS'
}

type Account = { id: number; name: string; is_active: boolean }
type Preview = {
  account: { id: number; name: string }
  filename: string
  source_row_count: number
  valid_row_count: number
  invalid_row_count: number
  valid_rows: {
    source_row: number
    date: string
    posted_date: string | null
    raw_description: string
    amount: string
    source_type: string
    classification: Transaction['classification']
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
}

const classificationLabels: Record<Transaction['classification'], string> = {
  UNKNOWN: 'Unknown',
  EXPENSE: 'Expense',
  INCOME: 'Income',
  REFUND: 'Refund',
  TRANSFER: 'Transfer',
  EXCLUDED: 'Excluded',
}

const bucketLabels: Record<Exclude<Transaction['budget_bucket'], ''>, string> = {
  NEEDS: 'Needs',
  WANTS: 'Wants',
  SAVINGS: 'Savings',
}

function formatAmount(amount: string) {
  const negative = amount.startsWith('-')
  const [whole, fraction = '00'] = (negative ? amount.slice(1) : amount).split('.')
  const groupedWhole = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  return `${negative ? '-' : ''}$${groupedWhole}.${fraction.padEnd(2, '0')}`
}

function App() {
  const [transactions, setTransactions] = useState<Transaction[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)
  const [showImport, setShowImport] = useState(false)
  const [accounts, setAccounts] = useState<Account[]>([])
  const [accountId, setAccountId] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [importResult, setImportResult] = useState<ImportResult | null>(null)
  const [importError, setImportError] = useState('')
  const [importing, setImporting] = useState(false)

  useEffect(() => {
    let active = true

    async function loadTransactions() {
      setLoading(true)
      setError('')
      try {
        const response = await apiFetch('/api/transactions/')
        if (!response.ok) throw new Error(`Request failed (${response.status})`)
        const result: Transaction[] = await response.json()
        if (active) setTransactions(result)
      } catch (loadError) {
        if (active) {
          setError(loadError instanceof Error ? loadError.message : 'Unable to load transactions')
        }
      } finally {
        if (active) setLoading(false)
      }
    }

    void loadTransactions()
    return () => {
      active = false
    }
  }, [reload])

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
          <a className="nav-item nav-item--active" href="#transactions" aria-current="page">
            <span className="nav-icon" aria-hidden="true">▤</span>
            Transactions
          </a>
        </nav>
        <p className="local-note">Your finances stay on this device.</p>
      </aside>

      <main className="main-content" id="transactions">
        <header className="page-header">
          <div>
            <p className="eyebrow">LEDGER</p>
            <h1>Transactions</h1>
            <p className="page-description">A clear view of activity across your accounts.</p>
          </div>
          <button
            className="refresh-button"
            type="button"
            onClick={() => setReload((current) => current + 1)}
            disabled={loading}
          >
            Refresh
          </button>
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
        </header>

        {showImport && (
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
                Imported {importResult.imported_row_count} of {importResult.source_row_count} rows
                from {importResult.filename} into {importResult.account}.
              </p>
            )}
            {preview && (
              <div className="preview-content">
                <p className="preview-summary">
                  {preview.valid_row_count} valid · {preview.invalid_row_count} invalid · Account: {preview.account.name}
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
                            <td className={`amount-cell ${row.amount.startsWith('-') ? 'amount-negative' : 'amount-positive'}`}>
                              {formatAmount(row.amount)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                {preview.valid_rows.some((row) => row.classification === 'UNKNOWN') && (
                  <p className="preview-warning">
                    Unknown classifications are valid and will be imported without guessing.
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

        <section className="ledger-card" aria-labelledby="ledger-heading">
          <div className="ledger-heading">
            <div>
              <h2 id="ledger-heading">All transactions</h2>
              <p>{loading ? 'Loading your ledger…' : `${transactions.length} transactions`}</p>
            </div>
          </div>

          {error ? (
            <div className="message message--error" role="alert">
              <span>{error}. Check that the backend is running.</span>
              <button type="button" onClick={() => setReload((current) => current + 1)}>
                Try again
              </button>
            </div>
          ) : loading ? (
            <p className="message" role="status">Loading transactions…</p>
          ) : transactions.length === 0 ? (
            <div className="empty-state">
              <span className="empty-icon" aria-hidden="true">↗</span>
              <h3>Your ledger is ready</h3>
              <p>Add an account and transactions in Django admin to see them here.</p>
              <a href="http://127.0.0.1:8000/admin/" target="_blank" rel="noreferrer">
                Open Django admin
              </a>
            </div>
          ) : (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th scope="col">Date</th>
                    <th scope="col">Description</th>
                    <th scope="col">Account</th>
                    <th scope="col">Classification</th>
                    <th scope="col">Category</th>
                    <th scope="col" className="amount-cell">Amount</th>
                  </tr>
                </thead>
                <tbody>
                  {transactions.map((transaction) => (
                    <tr key={transaction.id}>
                      <td className="date-cell">{transaction.date}</td>
                      <td>
                        <div className="description">
                          <span>{transaction.merchant || transaction.raw_description}</span>
                          {transaction.merchant && (
                            <small>{transaction.raw_description}</small>
                          )}
                        </div>
                      </td>
                      <td>{transaction.account_name}</td>
                      <td>
                        <span className={`classification classification--${transaction.classification.toLowerCase()}`}>
                          {classificationLabels[transaction.classification]}
                        </span>
                      </td>
                      <td>
                        {transaction.subcategory_name ? (
                          <span>
                            {transaction.subcategory_name}
                            {transaction.budget_bucket && (
                              <small className="bucket-label">
                                {bucketLabels[transaction.budget_bucket]}
                              </small>
                            )}
                          </span>
                        ) : <span className="muted">Uncategorized</span>}
                      </td>
                      <td className={`amount-cell ${transaction.amount.startsWith('-') ? 'amount-negative' : 'amount-positive'}`}>
                        {formatAmount(transaction.amount)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </main>
    </div>
  )
}

export default App

import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { apiFetch } from './api'

type AccountType = 'CHECKING' | 'SAVINGS' | 'CREDIT_CARD' | 'LOAN' | 'INVESTMENT' | 'OTHER'
type Account = {
  id: number
  name: string
  institution: string
  account_type: AccountType
  is_savings_destination: boolean
  is_active: boolean
}
type ImportBatch = {
  id: number
  account: number
  account_name: string
  original_filename: string
  format_identifier: string
  source_row_count: number
  imported_row_count: number
  already_represented_row_count: number
  possible_duplicate_row_count: number
  earliest_transaction_date: string | null
  latest_transaction_date: string | null
  imported_at: string
}
type AccountDraft = {
  name: string
  institution: string
  account_type: AccountType
  is_savings_destination: boolean
  is_active: boolean
}

const accountTypeLabels: Record<AccountType, string> = {
  CHECKING: 'Checking',
  SAVINGS: 'Savings',
  CREDIT_CARD: 'Credit card',
  LOAN: 'Loan',
  INVESTMENT: 'Investment',
  OTHER: 'Other',
}

function AccountTypeIcon({ type }: { type: AccountType }) {
  switch (type) {
    case 'CHECKING':
      return (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path d="M3 9.5L12 4l9 5.5v1.5H3V9.5z" fill="currentColor" fillOpacity="0.2" stroke="currentColor" strokeWidth="1.75" strokeLinejoin="round" />
          <path d="M5 11v7M9.5 11v7M14.5 11v7M19 11v7M3 18h18" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" />
        </svg>
      )
    case 'SAVINGS':
      return (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path d="M12 2L4 5.5v6c0 5.3 3.4 10.2 8 11.5 4.6-1.3 8-6.2 8-11.5v-6L12 2z" fill="currentColor" fillOpacity="0.2" stroke="currentColor" strokeWidth="1.75" strokeLinejoin="round" />
          <path d="M9 12l2 2 4-4" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      )
    case 'CREDIT_CARD':
      return (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <rect x="2.5" y="5" width="19" height="14" rx="2.5" fill="currentColor" fillOpacity="0.18" stroke="currentColor" strokeWidth="1.75" />
          <path d="M2.5 9.5h19M6.5 15h4" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" />
        </svg>
      )
    case 'LOAN':
      return (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <circle cx="12" cy="12" r="9" fill="currentColor" fillOpacity="0.16" stroke="currentColor" strokeWidth="1.75" />
          <path d="M12 7.5v9M9.5 10c0-1.1 1.1-2 2.5-2s2.5.9 2.5 2-1.1 1.8-2.5 2-2.5.9-2.5 2 1.1 2 2.5 2 2.5-.9 2.5-2" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" />
        </svg>
      )
    case 'INVESTMENT':
      return (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path d="M3 17l5.5-5.5 4 4L21 7" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" />
          <path d="M16 7h5v5" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      )
    default:
      return (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <rect x="3" y="6" width="18" height="13" rx="2.5" fill="currentColor" fillOpacity="0.18" stroke="currentColor" strokeWidth="1.75" />
          <path d="M16 12.5h2" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
        </svg>
      )
  }
}

const emptyDraft: AccountDraft = {
  name: '',
  institution: '',
  account_type: 'CHECKING',
  is_savings_destination: false,
  is_active: true,
}

function errorText(value: unknown): string {
  if (typeof value === 'string') return value
  if (value && typeof value === 'object') {
    const obj = value as Record<string, unknown>
    if (typeof obj.detail === 'string') return obj.detail
    const first = Object.entries(obj).find(([, v]) => Array.isArray(v) && typeof v[0] === 'string')
    if (first) {
      const [field, msgs] = first as [string, string[]]
      return `${field}: ${msgs[0]}`
    }
    return JSON.stringify(value)
  }
  return 'The request failed.'
}

export default function Accounts({ reload, onChanged }: { reload: number; onChanged: () => void }) {
  const [accounts, setAccounts] = useState<Account[]>([])
  const [batches, setBatches] = useState<ImportBatch[]>([])
  const [editingId, setEditingId] = useState<number | null>(null)
  const [formOpen, setFormOpen] = useState(false)
  const [draft, setDraft] = useState<AccountDraft>(emptyDraft)
  const [showInactive, setShowInactive] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  async function load() {
    const [accRes, batchRes] = await Promise.all([
      apiFetch('/api/accounts/'),
      apiFetch('/api/import-batches/'),
    ])
    const [accData, batchData] = await Promise.all([accRes.json(), batchRes.json()])
    if (!accRes.ok) throw new Error(errorText(accData))
    if (!batchRes.ok) throw new Error(errorText(batchData))
    setAccounts(accData as Account[])
    setBatches(batchData as ImportBatch[])
  }

  useEffect(() => {
    load().catch((err: unknown) => setError(err instanceof Error ? err.message : 'Unable to load accounts'))
  }, [reload])

  function beginEdit(account?: Account) {
    setError('')
    setNotice('')
    setEditingId(account?.id ?? null)
    setFormOpen(true)
    setDraft(account ? {
      name: account.name,
      institution: account.institution,
      account_type: account.account_type,
      is_savings_destination: account.is_savings_destination,
      is_active: account.is_active,
    } : emptyDraft)
  }

  async function save(event: FormEvent) {
    event.preventDefault()
    setError('')
    setNotice('')
    try {
      const response = await apiFetch(
        editingId === null ? '/api/accounts/' : `/api/accounts/${editingId}/`,
        {
          method: editingId === null ? 'POST' : 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(draft),
        },
      )
      const result = await response.json()
      if (!response.ok) throw new Error(errorText(result))
      setFormOpen(false)
      setEditingId(null)
      setNotice(editingId === null ? 'Account created.' : 'Account updated.')
      await load()
      onChanged()
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : 'Unable to save account')
    }
  }

  async function toggleActive(account: Account) {
    setError('')
    setNotice('')
    try {
      const response = await apiFetch(`/api/accounts/${account.id}/`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_active: !account.is_active }),
      })
      const result = await response.json()
      if (!response.ok) throw new Error(errorText(result))
      setNotice(account.is_active ? 'Account marked inactive.' : 'Account reactivated.')
      await load()
      onChanged()
    } catch (toggleError) {
      setError(toggleError instanceof Error ? toggleError.message : 'Unable to update account')
    }
  }

  const visibleAccounts = accounts.filter((account) => showInactive || account.is_active)
  const activeAccountsCount = accounts.filter((account) => account.is_active).length
  const savingsDestinationsCount = accounts.filter((account) => account.is_active && account.is_savings_destination).length
  const totalImportedRows = batches.reduce((acc, batch) => acc + batch.imported_row_count, 0)

  return (
    <div className="reports-panel">
      <div className="kpi-strip">
        <div className="kpi-card kpi-card--accent">
          <div className="kpi-card-header">
            <span className="kpi-label">Active Accounts</span>
            <span className="kpi-icon-badge" aria-hidden="true">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
                <rect x="2.5" y="5.5" width="19" height="13" rx="2.5" fill="currentColor" fillOpacity="0.18" stroke="currentColor" strokeWidth="1.75" />
                <path d="M2.5 10h19M6.5 14.5h3.5" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" />
              </svg>
            </span>
          </div>
          <strong className="kpi-value">{activeAccountsCount}</strong>
          <span className="kpi-sub">{accounts.length} total accounts</span>
        </div>
        <div className="kpi-card kpi-card--Savings">
          <div className="kpi-card-header">
            <span className="kpi-label">Savings Destinations</span>
            <span className="kpi-icon-badge" aria-hidden="true">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
                <path d="M12 2.5L4 6v5.8c0 5.1 3.4 9.8 8 11.2 4.6-1.4 8-6.1 8-11.2V6l-8-3.5z" fill="currentColor" fillOpacity="0.18" stroke="currentColor" strokeWidth="1.75" strokeLinejoin="round" />
                <path d="M9 12l2 2 4-4" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </span>
          </div>
          <strong className="kpi-value">{savingsDestinationsCount}</strong>
          <span className="kpi-sub">Active designated savings accounts</span>
        </div>
        <div className="kpi-card">
          <div className="kpi-card-header">
            <span className="kpi-label">Total Import Batches</span>
            <span className="kpi-icon-badge" aria-hidden="true">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
                <path d="M4 7.5L12 3l8 4.5-8 4.5-8-4.5z" fill="currentColor" fillOpacity="0.2" stroke="currentColor" strokeWidth="1.75" strokeLinejoin="round" />
                <path d="M4 12l8 4.5 8-4.5M4 16.5L12 21l8-4.5" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </span>
          </div>
          <strong className="kpi-value">{batches.length}</strong>
          <span className="kpi-sub">CSV statements imported</span>
        </div>
        <div className="kpi-card">
          <div className="kpi-card-header">
            <span className="kpi-label">Total Rows Imported</span>
            <span className="kpi-icon-badge" aria-hidden="true">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
                <rect x="4" y="3" width="16" height="18" rx="2.5" fill="currentColor" fillOpacity="0.16" stroke="currentColor" strokeWidth="1.75" />
                <path d="M8 8h8M8 12h8M8 16h5" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" />
              </svg>
            </span>
          </div>
          <strong className="kpi-value">{totalImportedRows.toLocaleString()}</strong>
          <span className="kpi-sub">Across all import batches</span>
        </div>
      </div>

      <section className="categories-panel" aria-labelledby="accounts-heading">
        <header className="categories-header">
          <div>
            <h2 id="accounts-heading">Accounts</h2>
            <p>Manage bank, credit card, savings, and loan accounts without opening Django admin.</p>
          </div>
          <div className="page-actions">
            <label className="archived-toggle">
              <input
                type="checkbox"
                checked={showInactive}
                onChange={(event) => setShowInactive(event.target.checked)}
              />
              Show inactive
            </label>
            <button className="primary-button" type="button" onClick={() => beginEdit()}>
              Add account
            </button>
          </div>
        </header>

        {error && <p className="message message--error" role="alert">{error}</p>}
        {notice && <p className="table-notice" role="status">{notice}</p>}

        {formOpen && (
          <form className="rule-editor" onSubmit={(event) => void save(event)}>
            <label>
              Account name
              <input
                value={draft.name}
                maxLength={100}
                required
                placeholder="Primary Checking"
                onChange={(event) => setDraft({ ...draft, name: event.target.value })}
              />
            </label>
            <label>
              Institution (optional)
              <input
                value={draft.institution}
                maxLength={100}
                placeholder="SoFi"
                onChange={(event) => setDraft({ ...draft, institution: event.target.value })}
              />
            </label>
            <label>
              Account type
              <select
                value={draft.account_type}
                onChange={(event) => setDraft({ ...draft, account_type: event.target.value as AccountType })}
              >
                {(Object.keys(accountTypeLabels) as AccountType[]).map((type) => (
                  <option key={type} value={type}>{accountTypeLabels[type]}</option>
                ))}
              </select>
            </label>
            <label className="remember-rule-toggle">
              <input
                type="checkbox"
                checked={draft.is_savings_destination}
                onChange={(event) => setDraft({ ...draft, is_savings_destination: event.target.checked })}
              />
              Designated savings destination
            </label>
            <div className="editor-actions">
              <button className="primary-button" type="submit">Save account</button>
              <button
                className="refresh-button"
                type="button"
                onClick={() => { setFormOpen(false); setEditingId(null) }}
              >
                Cancel
              </button>
            </div>
          </form>
        )}

        {visibleAccounts.length === 0 ? (
          <p className="message">No accounts found. Create an account to begin importing CSVs.</p>
        ) : (
          <>
            <div className="visual-card-grid">
              {visibleAccounts.map((account) => (
                <article
                  key={`card-${account.id}`}
                  className={`fintech-account-card fintech-account-card--${account.account_type} ${account.is_active ? '' : 'fintech-account-card--inactive'}`}
                >
                  <div className="fintech-card-header">
                    <span className="account-pill">
                      <AccountTypeIcon type={account.account_type} />
                      <span>{account.institution || 'Local Institution'}</span>
                    </span>
                    <span className="fintech-card-chip" aria-hidden="true">
                      <svg width="42" height="24" viewBox="0 0 42 24" fill="none">
                        <rect x="1" y="3" width="22" height="17" rx="3.5" fill="var(--accent)" fillOpacity="0.16" stroke="var(--accent)" strokeWidth="1.4" />
                        <path d="M1 9h22M1 14h22M9 3v17M15 3v17" stroke="var(--accent)" strokeWidth="1" strokeOpacity="0.45" />
                        <path d="M29 9.5a4.5 4.5 0 0 1 0 5M32.5 7.5a7.5 7.5 0 0 1 0 9M36 5.5a10.5 10.5 0 0 1 0 13" stroke="var(--muted)" strokeWidth="1.5" strokeLinecap="round" />
                      </svg>
                    </span>
                  </div>

                  <div>
                    <div className="fintech-card-institution">{account.institution || 'Local Account'}</div>
                    <div className="fintech-card-name">{account.name}</div>
                    <div className="fintech-card-badges" style={{ marginTop: '8px' }}>
                      <span className="fintech-card-badge">
                        {accountTypeLabels[account.account_type] || account.account_type}
                      </span>
                      {account.is_savings_destination && (
                        <span className="fintech-card-badge fintech-card-badge--savings">
                          Savings Destination
                        </span>
                      )}
                      <span className={`category-state ${account.is_active ? '' : 'category-state--archived'}`}>
                        {account.is_active ? 'Active' : 'Inactive'}
                      </span>
                    </div>
                  </div>

                  <div className="fintech-card-footer row-actions">
                    <button type="button" onClick={() => beginEdit(account)}>Edit</button>
                    <button type="button" className="quiet-button" onClick={() => void toggleActive(account)}>
                      {account.is_active ? 'Deactivate' : 'Activate'}
                    </button>
                  </div>
                </article>
              ))}
            </div>

            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th scope="col">Name</th>
                    <th scope="col">Institution</th>
                    <th scope="col">Type</th>
                    <th scope="col">Savings destination</th>
                    <th scope="col">Status</th>
                    <th scope="col">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleAccounts.map((account) => (
                    <tr key={account.id}>
                      <td><strong>{account.name}</strong></td>
                      <td>{account.institution || '—'}</td>
                      <td>{accountTypeLabels[account.account_type] || account.account_type}</td>
                      <td>{account.is_savings_destination ? 'Yes' : 'No'}</td>
                      <td>
                        <span className={`category-state ${account.is_active ? '' : 'category-state--archived'}`}>
                          {account.is_active ? 'Active' : 'Inactive'}
                        </span>
                      </td>
                      <td className="row-actions">
                        <button type="button" onClick={() => beginEdit(account)}>Edit</button>
                        <button type="button" className="quiet-button" onClick={() => void toggleActive(account)}>
                          {account.is_active ? 'Deactivate' : 'Activate'}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>

      <section className="categories-panel" aria-labelledby="batches-heading">
        <header className="categories-header">
          <div>
            <h2 id="batches-heading">Import history &amp; local backup</h2>
            <p>Audit every imported CSV batch with exact date coverage and overlap statistics, or download a snapshot of your local SQLite database.</p>
          </div>
          <div className="page-actions">
            <a className="secondary-button" href="/api/backup/sqlite/" download>
              Download SQLite backup
            </a>
          </div>
        </header>

        {batches.length === 0 ? (
          <p className="message">No CSV import batches yet.</p>
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th scope="col">Batch</th>
                  <th scope="col">Filename</th>
                  <th scope="col">Account</th>
                  <th scope="col">Transaction date range</th>
                  <th scope="col">Source rows</th>
                  <th scope="col">Imported</th>
                  <th scope="col">Already represented</th>
                  <th scope="col">Possible duplicates</th>
                  <th scope="col">Imported at</th>
                </tr>
              </thead>
              <tbody>
                {batches.map((batch) => (
                  <tr key={batch.id}>
                    <td>#{batch.id}</td>
                    <td><strong>{batch.original_filename}</strong></td>
                    <td>{batch.account_name}</td>
                    <td>
                      {batch.earliest_transaction_date && batch.latest_transaction_date
                        ? `${batch.earliest_transaction_date} → ${batch.latest_transaction_date}`
                        : '—'}
                    </td>
                    <td>{batch.source_row_count}</td>
                    <td>{batch.imported_row_count}</td>
                    <td>{batch.already_represented_row_count}</td>
                    <td>{batch.possible_duplicate_row_count}</td>
                    <td>{new Date(batch.imported_at).toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}

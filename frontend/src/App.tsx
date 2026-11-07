import { useEffect, useState } from 'react'
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

  useEffect(() => {
    let active = true

    async function loadTransactions() {
      setLoading(true)
      setError('')
      try {
        const response = await fetch('/api/transactions/')
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
        </header>

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

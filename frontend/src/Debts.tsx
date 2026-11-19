import { Fragment, useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { apiFetch } from './api'

type DebtType = 'CREDIT_CARD' | 'STUDENT_LOAN' | 'AUTO_LOAN' | 'PERSONAL_LOAN' | 'MORTGAGE' | 'OTHER'
type Snapshot = {
  id: number
  debt: number
  recorded_date: string
  balance: string
  notes: string
  created_at: string
}
type Debt = {
  id: number
  name: string
  lender: string
  debt_type: DebtType
  apr: string
  minimum_payment: string
  original_balance: string | null
  credit_limit: string | null
  due_day: number | null
  promo_apr_expiration: string | null
  is_active: boolean
  notes: string
  current_balance: string
  previous_balance: string | null
  balance_change: string | null
  paid_down_amount: string | null
  paid_down_percent: string | null
  snapshots: Snapshot[]
}
type DebtDraft = {
  name: string
  lender: string
  debt_type: DebtType
  apr: string
  minimum_payment: string
  original_balance: string
  credit_limit: string
  due_day: string
  promo_apr_expiration: string
  notes: string
  initial_balance: string
  initial_balance_date: string
}

const debtTypeLabels: Record<DebtType, string> = {
  CREDIT_CARD: 'Credit card',
  STUDENT_LOAN: 'Student loan',
  AUTO_LOAN: 'Auto loan',
  PERSONAL_LOAN: 'Personal loan',
  MORTGAGE: 'Mortgage',
  OTHER: 'Other',
}

function DebtTypeIcon({ type }: { type: DebtType }) {
  switch (type) {
    case 'STUDENT_LOAN':
      return (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path d="M12 4L2 9l10 5 10-5-10-5z" fill="currentColor" fillOpacity="0.2" stroke="currentColor" strokeWidth="1.75" strokeLinejoin="round" />
          <path d="M6 11.5v5c0 1.8 2.7 3.5 6 3.5s6-1.7 6-3.5v-5M22 9v6" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      )
    case 'AUTO_LOAN':
      return (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path d="M5 11l2-5h10l2 5H5z" fill="currentColor" fillOpacity="0.2" stroke="currentColor" strokeWidth="1.75" strokeLinejoin="round" />
          <rect x="3" y="11" width="18" height="6" rx="2" stroke="currentColor" strokeWidth="1.75" />
          <circle cx="7" cy="17" r="2" fill="currentColor" />
          <circle cx="17" cy="17" r="2" fill="currentColor" />
        </svg>
      )
    case 'CREDIT_CARD':
      return (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <rect x="2.5" y="5" width="19" height="14" rx="2.5" fill="currentColor" fillOpacity="0.18" stroke="currentColor" strokeWidth="1.75" />
          <path d="M2.5 9.5h19M6.5 15h4" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" />
        </svg>
      )
    case 'MORTGAGE':
      return (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path d="M3 10.5L12 3l9 7.5v9.5a1 1 0 0 1-1 1h-5.5v-6h-5v6H4a1 1 0 0 1-1-1v-9.5z" fill="currentColor" fillOpacity="0.2" stroke="currentColor" strokeWidth="1.75" strokeLinejoin="round" />
        </svg>
      )
    default:
      return (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <circle cx="12" cy="12" r="9" fill="currentColor" fillOpacity="0.16" stroke="currentColor" strokeWidth="1.75" />
          <path d="M12 7.5v9M9.5 10c0-1.1 1.1-2 2.5-2s2.5.9 2.5 2-1.1 1.8-2.5 2-2.5.9-2.5 2 1.1 2 2.5 2 2.5-.9 2.5-2" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" />
        </svg>
      )
  }
}

function PaydownRingGauge({ percent, size = 42 }: { percent: number; size?: number }) {
  const clamped = Math.max(0, Math.min(100, Math.round(percent)))
  const strokeWidth = 4
  const radius = (size - strokeWidth) / 2
  const circumference = 2 * Math.PI * radius
  const dashOffset = circumference - (clamped / 100) * circumference

  return (
    <svg
      className="kpi-ring-gauge"
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      aria-hidden="true"
      style={{ flexShrink: 0 }}
    >
      <circle
        cx={size / 2}
        cy={size / 2}
        r={radius}
        fill="none"
        stroke="var(--surface-alt)"
        strokeWidth={strokeWidth}
      />
      <circle
        cx={size / 2}
        cy={size / 2}
        r={radius}
        fill="none"
        stroke="var(--accent)"
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeDasharray={circumference}
        strokeDashoffset={dashOffset}
        transform={`rotate(-90 ${size / 2} ${size / 2})`}
      />
      <text
        x="50%"
        y="50%"
        dominantBaseline="central"
        textAnchor="middle"
        fill="var(--text)"
        fontSize="10"
        fontWeight="700"
      >
        {clamped}%
      </text>
    </svg>
  )
}

function DebtSnapshotSparkline({ snapshots }: { snapshots: Snapshot[] }) {
  if (snapshots.length < 2) return null
  const ordered = [...snapshots].sort((a, b) => a.recorded_date.localeCompare(b.recorded_date))
  const values = ordered.map((s) => Number(s.balance) || 0)
  const minVal = Math.min(...values)
  const maxVal = Math.max(...values)
  const range = maxVal - minVal || 1
  const width = 120
  const height = 28
  const pad = 3
  const points = values
    .map((val, idx) => {
      const x = pad + (idx / (values.length - 1)) * (width - pad * 2)
      const y = height - pad - ((val - minVal) / range) * (height - pad * 2)
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden="true" style={{ display: 'block' }}>
      <polyline
        fill="none"
        stroke="var(--accent)"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        points={points}
      />
    </svg>
  )
}

function todayIso() {
  return new Date().toISOString().slice(0, 10)
}

function emptyDraft(): DebtDraft {
  return {
    name: '',
    lender: '',
    debt_type: 'CREDIT_CARD',
    apr: '0.00',
    minimum_payment: '0.00',
    original_balance: '',
    credit_limit: '',
    due_day: '',
    promo_apr_expiration: '',
    notes: '',
    initial_balance: '',
    initial_balance_date: todayIso(),
  }
}

function formatAmount(amount: string) {
  const negative = amount.startsWith('-')
  const [whole, fraction = '00'] = (negative ? amount.slice(1) : amount).split('.')
  return `${negative ? '-' : ''}$${whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',')}.${fraction.padEnd(2, '0')}`
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

export default function Debts({ reload }: { reload: number }) {
  const [debts, setDebts] = useState<Debt[]>([])
  const [editingId, setEditingId] = useState<number | null>(null)
  const [formOpen, setFormOpen] = useState(false)
  const [draft, setDraft] = useState<DebtDraft>(emptyDraft)
  const [showInactive, setShowInactive] = useState(false)
  const [snapshotDebtId, setSnapshotDebtId] = useState<number | null>(null)
  const [snapshotBalance, setSnapshotBalance] = useState('')
  const [snapshotDate, setSnapshotDate] = useState(todayIso)
  const [snapshotNotes, setSnapshotNotes] = useState('')
  const [expandedHistoryId, setExpandedHistoryId] = useState<number | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  async function load() {
    const response = await apiFetch('/api/debts/')
    const result = await response.json()
    if (!response.ok) throw new Error(errorText(result))
    setDebts(result as Debt[])
  }

  useEffect(() => {
    load().catch((err: unknown) => setError(err instanceof Error ? err.message : 'Unable to load debts'))
  }, [reload])

  function beginEdit(debt?: Debt) {
    setError('')
    setNotice('')
    setEditingId(debt?.id ?? null)
    setFormOpen(true)
    setDraft(debt ? {
      name: debt.name,
      lender: debt.lender,
      debt_type: debt.debt_type,
      apr: debt.apr,
      minimum_payment: debt.minimum_payment,
      original_balance: debt.original_balance ?? '',
      credit_limit: debt.credit_limit ?? '',
      due_day: debt.due_day ? String(debt.due_day) : '',
      promo_apr_expiration: debt.promo_apr_expiration ?? '',
      notes: debt.notes,
      initial_balance: '',
      initial_balance_date: todayIso(),
    } : emptyDraft())
  }

  async function save(event: FormEvent) {
    event.preventDefault()
    setError('')
    setNotice('')
    const payload: Record<string, unknown> = {
      name: draft.name,
      lender: draft.lender,
      debt_type: draft.debt_type,
      apr: draft.apr || '0.00',
      minimum_payment: draft.minimum_payment || '0.00',
      original_balance: draft.original_balance.trim() ? draft.original_balance.trim() : null,
      credit_limit: draft.credit_limit.trim() ? draft.credit_limit.trim() : null,
      due_day: draft.due_day.trim() ? Number(draft.due_day) : null,
      promo_apr_expiration: draft.promo_apr_expiration.trim() ? draft.promo_apr_expiration.trim() : null,
      notes: draft.notes,
    }
    if (editingId === null && draft.initial_balance.trim()) {
      payload.initial_balance = draft.initial_balance.trim()
      payload.initial_balance_date = draft.initial_balance_date || todayIso()
    }
    try {
      const response = await apiFetch(
        editingId === null ? '/api/debts/' : `/api/debts/${editingId}/`,
        {
          method: editingId === null ? 'POST' : 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        },
      )
      const result = await response.json()
      if (!response.ok) throw new Error(errorText(result))
      setFormOpen(false)
      setEditingId(null)
      setNotice(editingId === null ? 'Debt added.' : 'Debt updated.')
      await load()
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : 'Unable to save debt')
    }
  }

  async function addSnapshot(event: FormEvent, debtId: number) {
    event.preventDefault()
    setError('')
    setNotice('')
    try {
      const response = await apiFetch(`/api/debts/${debtId}/snapshots/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          recorded_date: snapshotDate,
          balance: snapshotBalance,
          notes: snapshotNotes,
        }),
      })
      const result = await response.json()
      if (!response.ok) throw new Error(errorText(result))
      setSnapshotDebtId(null)
      setSnapshotBalance('')
      setSnapshotNotes('')
      setNotice('Balance snapshot recorded.')
      await load()
    } catch (snapError) {
      setError(snapError instanceof Error ? snapError.message : 'Unable to record balance snapshot')
    }
  }

  async function removeSnapshot(debtId: number, snapshotId: number) {
    setError('')
    setNotice('')
    try {
      const response = await apiFetch(`/api/debts/${debtId}/snapshots/${snapshotId}/`, {
        method: 'DELETE',
      })
      const result = await response.json()
      if (!response.ok) throw new Error(errorText(result))
      setNotice('Balance snapshot removed.')
      await load()
    } catch (removeError) {
      setError(removeError instanceof Error ? removeError.message : 'Unable to remove snapshot')
    }
  }

  async function toggleActive(debt: Debt) {
    setError('')
    setNotice('')
    try {
      const response = await apiFetch(`/api/debts/${debt.id}/`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_active: !debt.is_active }),
      })
      const result = await response.json()
      if (!response.ok) throw new Error(errorText(result))
      setNotice(debt.is_active ? 'Debt archived.' : 'Debt reactivated.')
      await load()
    } catch (toggleError) {
      setError(toggleError instanceof Error ? toggleError.message : 'Unable to update debt')
    }
  }

  const visible = debts.filter((debt) => showInactive || debt.is_active)
  const activeDebts = debts.filter((debt) => debt.is_active)
  const totalBalanceCents = activeDebts.reduce((acc, debt) => acc + Math.round(Number(debt.current_balance) * 100), 0)
  const totalMinimumCents = activeDebts.reduce((acc, debt) => acc + Math.round(Number(debt.minimum_payment) * 100), 0)
  const totalPaidDownCents = activeDebts.reduce(
    (acc, debt) => acc + (debt.paid_down_amount ? Math.round(Number(debt.paid_down_amount) * 100) : 0),
    0,
  )
  const totalBaselineCents = totalBalanceCents + Math.max(0, totalPaidDownCents)
  const overallPaidDownPct = totalBaselineCents > 0
    ? Math.max(0, Math.min(100, Math.round((Math.max(0, totalPaidDownCents) / totalBaselineCents) * 100)))
    : 0

  // Group student loans by servicer (lender) if multiple exist
  const studentLoanGroups = Object.entries(
    activeDebts
      .filter((debt) => debt.debt_type === 'STUDENT_LOAN' && debt.lender)
      .reduce<Record<string, { count: number; balanceCents: number; minPaymentCents: number }>>((acc, debt) => {
        const key = debt.lender
        const current = acc[key] ?? { count: 0, balanceCents: 0, minPaymentCents: 0 }
        current.count += 1
        current.balanceCents += Math.round(Number(debt.current_balance) * 100)
        current.minPaymentCents += Math.round(Number(debt.minimum_payment) * 100)
        acc[key] = current
        return acc
      }, {}),
  )

  return (
    <div className="reports-panel">
      <section className="categories-panel" aria-labelledby="debts-heading">
        <header className="categories-header">
          <div>
            <h2 id="debts-heading">Debts &amp; balance history</h2>
            <p>Track credit cards, student loans, and other liabilities with historical balance snapshots.</p>
          </div>
          <div className="page-actions">
            <label className="archived-toggle">
              <input
                type="checkbox"
                checked={showInactive}
                onChange={(event) => setShowInactive(event.target.checked)}
              />
              Show archived
            </label>
            <button className="primary-button" type="button" onClick={() => beginEdit()}>
              Add debt
            </button>
          </div>
        </header>

        {error && <p className="message message--error" role="alert">{error}</p>}
        {notice && <p className="table-notice" role="status">{notice}</p>}

        <div className="kpi-strip">
          <div className="kpi-card kpi-card--warning">
            <div className="kpi-card-header">
              <span className="kpi-label">Combined Current Balance</span>
              <span className="kpi-icon-badge" aria-hidden="true">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
                  <rect x="2.5" y="5" width="19" height="14" rx="2.5" fill="currentColor" fillOpacity="0.18" stroke="currentColor" strokeWidth="1.75" />
                  <path d="M2.5 9.5h19M6.5 14.5h4" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" />
                </svg>
              </span>
            </div>
            <strong className="kpi-value">{formatAmount((totalBalanceCents / 100).toFixed(2))}</strong>
            <span className="kpi-sub">Across active liabilities</span>
          </div>
          <div className="kpi-card kpi-card--Needs">
            <div className="kpi-card-header">
              <span className="kpi-label">Monthly Minimum Payments</span>
              <span className="kpi-icon-badge" aria-hidden="true">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
                  <rect x="3" y="4" width="18" height="17" rx="2.5" fill="currentColor" fillOpacity="0.18" stroke="currentColor" strokeWidth="1.75" />
                  <path d="M3 9h18M8 2.5v3M16 2.5v3M8 14h3M13 14h3" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" />
                </svg>
              </span>
            </div>
            <strong className="kpi-value">{formatAmount((totalMinimumCents / 100).toFixed(2))}</strong>
            <span className="kpi-sub">Required monthly obligation</span>
          </div>
          <div className="kpi-card kpi-card--accent">
            <div className="kpi-card-header">
              <span className="kpi-label">Total Paid Down</span>
              <span className="kpi-icon-badge" aria-hidden="true">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
                  <path d="M3 7l6 6 4-4 8 8" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" />
                  <path d="M15 17h6v-6" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '10px' }}>
              <div>
                <strong className="kpi-value">{formatAmount((totalPaidDownCents / 100).toFixed(2))}</strong>
                <span className="kpi-sub">{overallPaidDownPct}% of tracked baseline</span>
              </div>
              <PaydownRingGauge percent={overallPaidDownPct} size={42} />
            </div>
            <div className="progress-track">
              <div className="progress-fill" style={{ width: `${overallPaidDownPct}%` }} />
            </div>
          </div>
          <div className="kpi-card">
            <div className="kpi-card-header">
              <span className="kpi-label">Active Debts Count</span>
              <span className="kpi-icon-badge" aria-hidden="true">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
                  <path d="M12 3L3 7.5 12 12l9-4.5L12 3z" fill="currentColor" fillOpacity="0.18" stroke="currentColor" strokeWidth="1.75" strokeLinejoin="round" />
                  <path d="M3 12l9 4.5 9-4.5M3 16.5L12 21l9-4.5" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </span>
            </div>
            <strong className="kpi-value">{activeDebts.length}</strong>
            <span className="kpi-sub">{debts.length - activeDebts.length} archived</span>
          </div>
        </div>

        <div
          className="report-breakdowns"
          style={{
            padding: '14px',
            gridTemplateColumns: studentLoanGroups.length > 0 ? 'repeat(2, minmax(0, 1fr))' : 'minmax(0, 1fr)',
          }}
        >
          <section className="table-scroll" style={{ padding: '12px' }}>
            <h3>Active debt summary</h3>
            <table>
              <tbody>
                <tr>
                  <th scope="row">Active debts</th>
                  <td className="amount-cell">{activeDebts.length}</td>
                </tr>
                <tr>
                  <th scope="row">Combined current balance</th>
                  <td className="amount-cell"><strong>{formatAmount((totalBalanceCents / 100).toFixed(2))}</strong></td>
                </tr>
                <tr>
                  <th scope="row">Total monthly minimum payments</th>
                  <td className="amount-cell">{formatAmount((totalMinimumCents / 100).toFixed(2))}</td>
                </tr>
                <tr>
                  <th scope="row">Total paid down from baseline</th>
                  <td className="amount-cell">{formatAmount((totalPaidDownCents / 100).toFixed(2))}</td>
                </tr>
              </tbody>
            </table>
          </section>

          {studentLoanGroups.length > 0 && (
            <section className="table-scroll" style={{ padding: '12px' }}>
              <h3>Combined student loan servicers</h3>
              <table>
                <thead>
                  <tr>
                    <th>Servicer</th>
                    <th>Loans</th>
                    <th className="amount-cell">Balance</th>
                    <th className="amount-cell">Min payment</th>
                  </tr>
                </thead>
                <tbody>
                  {studentLoanGroups.map(([servicer, group]) => (
                    <tr key={servicer}>
                      <th scope="row">{servicer}</th>
                      <td>{group.count}</td>
                      <td className="amount-cell">{formatAmount((group.balanceCents / 100).toFixed(2))}</td>
                      <td className="amount-cell">{formatAmount((group.minPaymentCents / 100).toFixed(2))}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          )}
        </div>

        {formOpen && (
          <form className="rule-editor" onSubmit={(event) => void save(event)}>
            <label>
              Name
              <input
                value={draft.name}
                maxLength={100}
                required
                placeholder="Loan AA or Visa Rewards"
                onChange={(event) => setDraft({ ...draft, name: event.target.value })}
              />
            </label>
            <label>
              Lender / Servicer
              <input
                value={draft.lender}
                maxLength={100}
                placeholder="Example Student Loan Servicer"
                onChange={(event) => setDraft({ ...draft, lender: event.target.value })}
              />
            </label>
            <label>
              Debt type
              <select
                value={draft.debt_type}
                onChange={(event) => setDraft({ ...draft, debt_type: event.target.value as DebtType })}
              >
                {(Object.keys(debtTypeLabels) as DebtType[]).map((type) => (
                  <option key={type} value={type}>{debtTypeLabels[type]}</option>
                ))}
              </select>
            </label>
            <label>
              APR (%)
              <input
                type="number"
                step="0.01"
                min="0"
                max="100"
                value={draft.apr}
                onChange={(event) => setDraft({ ...draft, apr: event.target.value })}
              />
            </label>
            <label>
              Minimum payment ($)
              <input
                type="number"
                step="0.01"
                min="0"
                value={draft.minimum_payment}
                onChange={(event) => setDraft({ ...draft, minimum_payment: event.target.value })}
              />
            </label>
            {editingId === null && (
              <>
                <label>
                  Current balance ($)
                  <input
                    type="number"
                    step="0.01"
                    min="0"
                    placeholder="8500.00"
                    value={draft.initial_balance}
                    onChange={(event) => setDraft({ ...draft, initial_balance: event.target.value })}
                  />
                </label>
                <label>
                  As-of date
                  <input
                    type="date"
                    value={draft.initial_balance_date}
                    onChange={(event) => setDraft({ ...draft, initial_balance_date: event.target.value })}
                  />
                </label>
              </>
            )}
            <label>
              Original balance ($ optional)
              <input
                type="number"
                step="0.01"
                min="0"
                placeholder="Defaults to initial snapshot"
                value={draft.original_balance}
                onChange={(event) => setDraft({ ...draft, original_balance: event.target.value })}
              />
            </label>
            <label>
              Credit limit ($ optional)
              <input
                type="number"
                step="0.01"
                min="0.01"
                value={draft.credit_limit}
                onChange={(event) => setDraft({ ...draft, credit_limit: event.target.value })}
              />
            </label>
            <label>
              Due day of month (1–31)
              <input
                type="number"
                min="1"
                max="31"
                value={draft.due_day}
                onChange={(event) => setDraft({ ...draft, due_day: event.target.value })}
              />
            </label>
            <label>
              Notes
              <input
                value={draft.notes}
                onChange={(event) => setDraft({ ...draft, notes: event.target.value })}
              />
            </label>
            <div className="editor-actions">
              <button className="primary-button" type="submit">Save debt</button>
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

        {visible.length === 0 ? (
          <p className="message">No debts tracked yet.</p>
        ) : (
          <>
            <div className="visual-card-grid" style={{ padding: '0 14px 10px' }}>
              {visible.map((debt) => {
                const paidPct = debt.paid_down_percent !== null ? Math.max(0, Math.min(100, Number(debt.paid_down_percent) || 0)) : 0
                return (
                  <article
                    key={`paydown-card-${debt.id}`}
                    className="paydown-ring-card"
                    style={{ opacity: debt.is_active ? 1 : 0.72 }}
                  >
                    <div className="paydown-card-header">
                      <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                        <span className="kpi-icon-badge" aria-hidden="true">
                          <DebtTypeIcon type={debt.debt_type} />
                        </span>
                        <div>
                          <div style={{ fontSize: '14px', fontWeight: 700, color: 'var(--text)' }}>{debt.name}</div>
                          <div style={{ fontSize: '11.5px', color: 'var(--muted)' }}>
                            {debt.lender ? `${debt.lender} · ` : ''}{debtTypeLabels[debt.debt_type] || debt.debt_type}
                          </div>
                        </div>
                      </div>
                      <PaydownRingGauge percent={paidPct} size={42} />
                    </div>

                    <div className="paydown-card-body">
                      <div className="paydown-card-metrics">
                        <div style={{ fontSize: '18px', fontWeight: 700, color: 'var(--text)', fontVariantNumeric: 'tabular-nums' }}>
                          {formatAmount(debt.current_balance)}
                        </div>
                        <div style={{ fontSize: '11px', color: 'var(--muted)' }}>
                          {debt.original_balance ? `of ${formatAmount(debt.original_balance)} original` : 'Current balance'}
                        </div>
                      </div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap' }}>
                        <span className="paydown-apr-badge">{debt.apr}% APR</span>
                        {debt.due_day && <span className="bucket-label" style={{ margin: 0 }}>Due day {debt.due_day}</span>}
                      </div>
                    </div>

                    {debt.snapshots.length >= 2 && (
                      <div className="paydown-card-footer">
                        <span>Balance trend ({debt.snapshots.length} snapshots)</span>
                        <DebtSnapshotSparkline snapshots={debt.snapshots} />
                      </div>
                    )}
                  </article>
                )
              })}
            </div>

            <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th scope="col">Name &amp; Lender</th>
                  <th scope="col">Type</th>
                  <th scope="col" className="amount-cell">Current balance</th>
                  <th scope="col" className="amount-cell">Original</th>
                  <th scope="col" className="amount-cell">Paid down</th>
                  <th scope="col" className="amount-cell">Since previous</th>
                  <th scope="col" className="amount-cell">APR</th>
                  <th scope="col" className="amount-cell">Min payment</th>
                  <th scope="col">Due day</th>
                  <th scope="col">Actions</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((debt) => (
                  <Fragment key={debt.id}>
                    <tr>
                      <td>
                        <div className="description">
                          <span><strong>{debt.name}</strong></span>
                          {debt.lender && <small>{debt.lender}</small>}
                          {debt.notes && <small>{debt.notes}</small>}
                        </div>
                      </td>
                      <td>{debtTypeLabels[debt.debt_type] || debt.debt_type}</td>
                      <td className="amount-cell"><strong>{formatAmount(debt.current_balance)}</strong></td>
                      <td className="amount-cell">{debt.original_balance ? formatAmount(debt.original_balance) : '—'}</td>
                      <td className="amount-cell">
                        {debt.paid_down_amount ? (
                          <div>
                            <div>
                              {formatAmount(debt.paid_down_amount)}
                              {debt.paid_down_percent ? ` (${debt.paid_down_percent}%)` : ''}
                            </div>
                            {debt.paid_down_percent !== null && (
                              <div className="progress-track">
                                <div
                                  className="progress-fill"
                                  style={{
                                    width: `${Math.max(0, Math.min(100, Number(debt.paid_down_percent) || 0))}%`,
                                  }}
                                />
                              </div>
                            )}
                          </div>
                        ) : '—'}
                      </td>
                      <td className="amount-cell">
                        {debt.balance_change
                          ? formatAmount(debt.balance_change)
                          : '—'}
                      </td>
                      <td className="amount-cell">{debt.apr}%</td>
                      <td className="amount-cell">{formatAmount(debt.minimum_payment)}</td>
                      <td>{debt.due_day ? `Day ${debt.due_day}` : '—'}</td>
                      <td className="row-actions">
                        <button
                          type="button"
                          onClick={() => {
                            setSnapshotDebtId(snapshotDebtId === debt.id ? null : debt.id)
                            setSnapshotBalance(debt.current_balance)
                            setSnapshotDate(todayIso())
                            setSnapshotNotes('')
                          }}
                        >
                          Update balance
                        </button>
                        <button
                          type="button"
                          onClick={() => setExpandedHistoryId(expandedHistoryId === debt.id ? null : debt.id)}
                        >
                          History ({debt.snapshots.length})
                        </button>
                        <button type="button" onClick={() => beginEdit(debt)}>Edit</button>
                        <button type="button" className="quiet-button" onClick={() => void toggleActive(debt)}>
                          {debt.is_active ? 'Archive' : 'Reactivate'}
                        </button>
                      </td>
                    </tr>
                    {snapshotDebtId === debt.id && (
                      <tr className="detail-row">
                        <td colSpan={10}>
                          <form className="transaction-editor" onSubmit={(event) => void addSnapshot(event, debt.id)}>
                            <label>
                              New balance ($)
                              <input
                                type="number"
                                step="0.01"
                                min="0"
                                required
                                value={snapshotBalance}
                                onChange={(event) => setSnapshotBalance(event.target.value)}
                              />
                            </label>
                            <label>
                              Snapshot date
                              <input
                                type="date"
                                required
                                value={snapshotDate}
                                onChange={(event) => setSnapshotDate(event.target.value)}
                              />
                            </label>
                            <label>
                              Note (optional)
                              <input
                                value={snapshotNotes}
                                placeholder="Monthly statement update"
                                onChange={(event) => setSnapshotNotes(event.target.value)}
                              />
                            </label>
                            <div className="editor-actions">
                              <button className="primary-button" type="submit">Record snapshot</button>
                              <button
                                className="refresh-button"
                                type="button"
                                onClick={() => setSnapshotDebtId(null)}
                              >
                                Cancel
                              </button>
                            </div>
                          </form>
                        </td>
                      </tr>
                    )}
                    {expandedHistoryId === debt.id && (
                      <tr className="detail-row">
                        <td colSpan={10}>
                          <div style={{ padding: '10px 14px' }}>
                            <strong>Balance snapshot history for {debt.name}</strong>
                            {debt.snapshots.length === 0 ? (
                              <p className="message">No balance snapshots recorded yet.</p>
                            ) : (
                              <table style={{ marginTop: '8px' }}>
                                <thead>
                                  <tr>
                                    <th>Date</th>
                                    <th className="amount-cell">Balance</th>
                                    <th>Notes</th>
                                    <th>Actions</th>
                                  </tr>
                                </thead>
                                <tbody>
                                  {debt.snapshots.map((snap) => (
                                    <tr key={snap.id}>
                                      <td>{snap.recorded_date}</td>
                                      <td className="amount-cell">{formatAmount(snap.balance)}</td>
                                      <td>{snap.notes || '—'}</td>
                                      <td>
                                        <button
                                          type="button"
                                          className="quiet-button"
                                          onClick={() => void removeSnapshot(debt.id, snap.id)}
                                        >
                                          Remove
                                        </button>
                                      </td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            )}
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
          </>
        )}
      </section>
    </div>
  )
}

import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { apiFetch } from './api'

type Kind = 'BILL' | 'MONTHLY_SUBSCRIPTION' | 'ANNUAL_SUBSCRIPTION'
type Bucket = 'NEEDS' | 'WANTS' | 'SAVINGS'
type Subcategory = {
  id: number
  name: string
  scope: 'BUDGET' | 'OTHER_INFLOW'
  budget_bucket: Bucket | null
  is_active: boolean
}
type RecurringItem = {
  id: number
  name: string
  kind: Kind
  amount: string
  due_day: number | null
  renewal_date: string | null
  subcategory: number | null
  subcategory_name: string | null
  budget_bucket: Bucket | null
  auto_pay: boolean
  is_active: boolean
  notes: string
  effective_monthly_cost: string
  effective_annual_cost: string
}
type RecurringSummary = {
  active_count: number
  monthly_bills_total: string
  monthly_subscriptions_total: string
  annual_subscriptions_total: string
  effective_monthly_total: string
  effective_annual_total: string
}
type Draft = {
  name: string
  kind: Kind
  amount: string
  due_day: string
  renewal_date: string
  subcategory: string
  auto_pay: boolean
  notes: string
}

const kindLabels: Record<Kind, string> = {
  BILL: 'Monthly bill',
  MONTHLY_SUBSCRIPTION: 'Monthly subscription',
  ANNUAL_SUBSCRIPTION: 'Annual subscription',
}

const bucketLabels: Record<Bucket, string> = {
  NEEDS: 'Needs',
  WANTS: 'Wants',
  SAVINGS: 'Savings',
}

function ServiceIcon({ kind }: { kind: Kind }) {
  switch (kind) {
    case 'BILL':
      return (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path d="M13 2L4 14h7l-1 8 10-12h-7l0-8z" fill="currentColor" fillOpacity="0.22" stroke="currentColor" strokeWidth="1.75" strokeLinejoin="round" />
        </svg>
      )
    case 'MONTHLY_SUBSCRIPTION':
      return (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <circle cx="12" cy="12" r="9" fill="currentColor" fillOpacity="0.18" stroke="currentColor" strokeWidth="1.75" />
          <path d="M10 8.5l6 3.5-6 3.5v-7z" fill="currentColor" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
        </svg>
      )
    case 'ANNUAL_SUBSCRIPTION':
      return (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <rect x="3" y="4.5" width="18" height="16.5" rx="2.5" fill="currentColor" fillOpacity="0.16" stroke="currentColor" strokeWidth="1.75" />
          <path d="M3 9.5h18M8 3v3M16 3v3" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" />
          <path d="M12 12.5l1.1 2.2 2.4.35-1.75 1.7.4 2.4-2.15-1.15-2.15 1.15.4-2.4-1.75-1.7 2.4-.35L12 12.5z" fill="currentColor" />
        </svg>
      )
  }
}

const emptyDraft: Draft = {
  name: '',
  kind: 'BILL',
  amount: '',
  due_day: '',
  renewal_date: '',
  subcategory: '',
  auto_pay: false,
  notes: '',
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

export default function Recurring({ reload }: { reload: number }) {
  const [items, setItems] = useState<RecurringItem[]>([])
  const [summary, setSummary] = useState<RecurringSummary | null>(null)
  const [categories, setCategories] = useState<Subcategory[]>([])
  const [editingId, setEditingId] = useState<number | null>(null)
  const [formOpen, setFormOpen] = useState(false)
  const [draft, setDraft] = useState<Draft>(emptyDraft)
  const [showInactive, setShowInactive] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  async function load() {
    const [itemsRes, sumRes, catRes] = await Promise.all([
      apiFetch('/api/recurring-expenses/'),
      apiFetch('/api/recurring-expenses/summary/'),
      apiFetch('/api/subcategories/'),
    ])
    const [itemsData, sumData, catData] = await Promise.all([
      itemsRes.json(),
      sumRes.json(),
      catRes.json(),
    ])
    if (!itemsRes.ok) throw new Error(errorText(itemsData))
    if (!sumRes.ok) throw new Error(errorText(sumData))
    if (!catRes.ok) throw new Error(errorText(catData))
    setItems(itemsData as RecurringItem[])
    setSummary(sumData as RecurringSummary)
    setCategories(catData as Subcategory[])
  }

  useEffect(() => {
    load().catch((err: unknown) => setError(err instanceof Error ? err.message : 'Unable to load recurring expenses'))
  }, [reload])

  function beginEdit(item?: RecurringItem) {
    setError('')
    setNotice('')
    setEditingId(item?.id ?? null)
    setFormOpen(true)
    setDraft(item ? {
      name: item.name,
      kind: item.kind,
      amount: item.amount,
      due_day: item.due_day ? String(item.due_day) : '',
      renewal_date: item.renewal_date ?? '',
      subcategory: item.subcategory ? String(item.subcategory) : '',
      auto_pay: item.auto_pay,
      notes: item.notes,
    } : emptyDraft)
  }

  async function save(event: FormEvent) {
    event.preventDefault()
    setError('')
    setNotice('')
    const payload = {
      name: draft.name,
      kind: draft.kind,
      amount: draft.amount,
      due_day: draft.kind !== 'ANNUAL_SUBSCRIPTION' && draft.due_day.trim() ? Number(draft.due_day) : null,
      renewal_date: draft.kind === 'ANNUAL_SUBSCRIPTION' && draft.renewal_date.trim() ? draft.renewal_date.trim() : null,
      subcategory: draft.subcategory ? Number(draft.subcategory) : null,
      auto_pay: draft.auto_pay,
      notes: draft.notes,
    }
    try {
      const response = await apiFetch(
        editingId === null ? '/api/recurring-expenses/' : `/api/recurring-expenses/${editingId}/`,
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
      setNotice(editingId === null ? 'Recurring item added.' : 'Recurring item updated.')
      await load()
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : 'Unable to save recurring item')
    }
  }

  async function toggleActive(item: RecurringItem) {
    setError('')
    setNotice('')
    try {
      const response = await apiFetch(`/api/recurring-expenses/${item.id}/`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_active: !item.is_active }),
      })
      const result = await response.json()
      if (!response.ok) throw new Error(errorText(result))
      setNotice(item.is_active ? 'Recurring item archived.' : 'Recurring item reactivated.')
      await load()
    } catch (toggleError) {
      setError(toggleError instanceof Error ? toggleError.message : 'Unable to update recurring item')
    }
  }

  const eligibleCategories = categories.filter(
    (cat) => cat.is_active && cat.scope === 'BUDGET' && (cat.budget_bucket === 'NEEDS' || cat.budget_bucket === 'WANTS'),
  )
  const visible = items.filter((item) => showInactive || item.is_active)

  return (
    <div className="reports-panel">
      <section className="categories-panel" aria-labelledby="recurring-heading">
        <header className="categories-header">
          <div>
            <h2 id="recurring-heading">Recurring bills &amp; subscriptions</h2>
            <p>Track monthly bills, monthly subscriptions, and annual renewals with effective monthly and annual costs.</p>
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
              Add recurring item
            </button>
          </div>
        </header>

        {error && <p className="message message--error" role="alert">{error}</p>}
        {notice && <p className="table-notice" role="status">{notice}</p>}

        {summary && (() => {
          const billsMonthly = Math.max(0, Number(summary.monthly_bills_total) || 0)
          const effMonthly = Math.max(0, Number(summary.effective_monthly_total) || 0)
          const subsEffectiveMonthly = Math.max(0, effMonthly - billsMonthly)
          const billsPct = effMonthly > 0 ? Math.round((billsMonthly / effMonthly) * 100) : 0
          const subsPct = effMonthly > 0 ? Math.max(0, 100 - billsPct) : 0

          return (
            <>
              <div className="kpi-strip">
                <div className="kpi-card kpi-card--accent">
                  <div className="kpi-card-header">
                    <span className="kpi-label">Effective Monthly Total</span>
                    <span className="kpi-icon-badge" aria-hidden="true">
                      <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
                        <path d="M17 2l4 4-4 4" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" />
                        <path d="M3 11V9a4 4 0 0 1 4-4h14M7 22l-4-4 4-4" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" />
                        <path d="M21 13v2a4 4 0 0 1-4 4H3" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" />
                      </svg>
                    </span>
                  </div>
                  <strong className="kpi-value">{formatAmount(summary.effective_monthly_total)} / mo</strong>
                  <span className="kpi-sub">{summary.active_count} active recurring items</span>
                </div>
                <div className="kpi-card">
                  <div className="kpi-card-header">
                    <span className="kpi-label">Effective Annual Total</span>
                    <span className="kpi-icon-badge" aria-hidden="true">
                      <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
                        <rect x="3" y="4.5" width="18" height="16.5" rx="2.5" fill="currentColor" fillOpacity="0.16" stroke="currentColor" strokeWidth="1.75" />
                        <path d="M3 9.5h18M8 3v3M16 3v3" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" />
                      </svg>
                    </span>
                  </div>
                  <strong className="kpi-value">{formatAmount(summary.effective_annual_total)} / yr</strong>
                  <span className="kpi-sub">Annualized recurring run-rate</span>
                </div>
                <div className="kpi-card kpi-card--Needs">
                  <div className="kpi-card-header">
                    <span className="kpi-label">Monthly Bills</span>
                    <span className="kpi-icon-badge" aria-hidden="true">
                      <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
                        <path d="M13 2L4 14h7l-1 8 10-12h-7l0-8z" fill="currentColor" fillOpacity="0.2" stroke="currentColor" strokeWidth="1.75" strokeLinejoin="round" />
                      </svg>
                    </span>
                  </div>
                  <strong className="kpi-value">{formatAmount(summary.monthly_bills_total)} / mo</strong>
                  <span className="kpi-sub">Fixed monthly bills</span>
                </div>
                <div className="kpi-card kpi-card--Wants">
                  <div className="kpi-card-header">
                    <span className="kpi-label">Subscriptions (Monthly + Annual)</span>
                    <span className="kpi-icon-badge" aria-hidden="true">
                      <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
                        <circle cx="12" cy="12" r="9" fill="currentColor" fillOpacity="0.18" stroke="currentColor" strokeWidth="1.75" />
                        <path d="M10 8.5l6 3.5-6 3.5v-7z" fill="currentColor" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
                      </svg>
                    </span>
                  </div>
                  <strong className="kpi-value">{formatAmount(summary.monthly_subscriptions_total)} / mo</strong>
                  <span className="kpi-sub">+ {formatAmount(summary.annual_subscriptions_total)} / yr renewals</span>
                </div>
              </div>

              <div style={{ padding: '14px 14px 0' }}>
                <div
                  style={{
                    border: '1px solid var(--border)',
                    borderRadius: '12px',
                    padding: '12px 14px',
                    background: 'var(--surface-alt)',
                    display: 'grid',
                    gap: '8px',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '10px', flexWrap: 'wrap', fontSize: '12px' }}>
                    <strong style={{ color: 'var(--text)' }}>Monthly Cost Breakdown: Bills vs Subscriptions</strong>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '14px', color: 'var(--text-secondary)', fontSize: '11.5px' }}>
                      <span style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
                        <span style={{ width: '9px', height: '9px', borderRadius: '3px', background: 'var(--chart-needs)', display: 'inline-block' }} aria-hidden="true" />
                        Monthly Bills: <strong>{formatAmount(summary.monthly_bills_total)}/mo</strong> ({billsPct}%)
                      </span>
                      <span style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
                        <span style={{ width: '9px', height: '9px', borderRadius: '3px', background: 'var(--chart-wants)', display: 'inline-block' }} aria-hidden="true" />
                        Subscriptions (effective): <strong>{formatAmount(subsEffectiveMonthly.toFixed(2))}/mo</strong> ({subsPct}%)
                      </span>
                    </div>
                  </div>
                  <div
                    className="kpi-spark-bar"
                    style={{
                      display: 'flex',
                      height: '10px',
                      width: '100%',
                      borderRadius: '999px',
                      overflow: 'hidden',
                      background: 'var(--surface)',
                      border: '1px solid var(--border)',
                    }}
                    aria-hidden="true"
                  >
                    <div style={{ width: `${billsPct}%`, background: 'var(--chart-needs)', transition: 'width 200ms ease' }} />
                    <div style={{ width: `${subsPct}%`, background: 'var(--chart-wants)', transition: 'width 200ms ease' }} />
                  </div>
                </div>
              </div>

              <div className="report-breakdowns" style={{ padding: '14px', gridTemplateColumns: 'minmax(0, 1fr)' }}>
                <section className="table-scroll" style={{ padding: '12px' }}>
                  <h3>Recurring cost summary ({summary.active_count} active)</h3>
                  <table>
                    <tbody>
                      <tr>
                        <th scope="row">Monthly bills total</th>
                        <td className="amount-cell">{formatAmount(summary.monthly_bills_total)} / mo</td>
                      </tr>
                      <tr>
                        <th scope="row">Monthly subscriptions total</th>
                        <td className="amount-cell">{formatAmount(summary.monthly_subscriptions_total)} / mo</td>
                      </tr>
                      <tr>
                        <th scope="row">Annual subscriptions total</th>
                        <td className="amount-cell">{formatAmount(summary.annual_subscriptions_total)} / yr</td>
                      </tr>
                      <tr className="remaining-row">
                        <th scope="row">Combined effective monthly cost</th>
                        <td className="amount-cell"><strong>{formatAmount(summary.effective_monthly_total)} / mo</strong></td>
                      </tr>
                      <tr className="remaining-row">
                        <th scope="row">Combined effective annual cost</th>
                        <td className="amount-cell"><strong>{formatAmount(summary.effective_annual_total)} / yr</strong></td>
                      </tr>
                    </tbody>
                  </table>
                </section>
              </div>
            </>
          )
        })()}

        {formOpen && (
          <form className="rule-editor" onSubmit={(event) => void save(event)}>
            <label>
              Name
              <input
                value={draft.name}
                maxLength={100}
                required
                placeholder="Rent, Electric, or Amazon Prime"
                onChange={(event) => setDraft({ ...draft, name: event.target.value })}
              />
            </label>
            <label>
              Kind
              <select
                value={draft.kind}
                onChange={(event) => setDraft({ ...draft, kind: event.target.value as Kind })}
              >
                {(Object.keys(kindLabels) as Kind[]).map((k) => (
                  <option key={k} value={k}>{kindLabels[k]}</option>
                ))}
              </select>
            </label>
            <label>
              Amount ($)
              <input
                type="number"
                step="0.01"
                min="0.01"
                required
                placeholder="150.00"
                value={draft.amount}
                onChange={(event) => setDraft({ ...draft, amount: event.target.value })}
              />
            </label>
            {draft.kind !== 'ANNUAL_SUBSCRIPTION' ? (
              <label>
                Due / billing day (1–31)
                <input
                  type="number"
                  min="1"
                  max="31"
                  value={draft.due_day}
                  onChange={(event) => setDraft({ ...draft, due_day: event.target.value })}
                />
              </label>
            ) : (
              <label>
                Next renewal date
                <input
                  type="date"
                  value={draft.renewal_date}
                  onChange={(event) => setDraft({ ...draft, renewal_date: event.target.value })}
                />
              </label>
            )}
            <label>
              Subcategory (optional)
              <select
                value={draft.subcategory}
                onChange={(event) => setDraft({ ...draft, subcategory: event.target.value })}
              >
                <option value="">None</option>
                {eligibleCategories.map((cat) => (
                  <option key={cat.id} value={cat.id}>
                    {cat.name} · {cat.budget_bucket ? bucketLabels[cat.budget_bucket] : ''}
                  </option>
                ))}
              </select>
            </label>
            <label className="remember-rule-toggle">
              <input
                type="checkbox"
                checked={draft.auto_pay}
                onChange={(event) => setDraft({ ...draft, auto_pay: event.target.checked })}
              />
              Auto-pay / Auto-renew enabled
            </label>
            <label>
              Notes
              <input
                value={draft.notes}
                onChange={(event) => setDraft({ ...draft, notes: event.target.value })}
              />
            </label>
            <div className="editor-actions">
              <button className="primary-button" type="submit">Save recurring item</button>
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

        {(['BILL', 'MONTHLY_SUBSCRIPTION', 'ANNUAL_SUBSCRIPTION'] as Kind[]).map((kindKey) => {
          const group = visible.filter((item) => item.kind === kindKey)
          if (!group.length) return null
          return (
            <div key={kindKey} style={{ padding: '0 14px 16px' }}>
              <h3 style={{ margin: '12px 0 8px', fontSize: '14px' }}>{kindLabels[kindKey]}s</h3>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th scope="col">Name</th>
                      <th scope="col">Subcategory</th>
                      <th scope="col">{kindKey === 'ANNUAL_SUBSCRIPTION' ? 'Renewal date' : 'Due day'}</th>
                      <th scope="col">Auto-pay</th>
                      <th scope="col" className="amount-cell">Billed amount</th>
                      <th scope="col" className="amount-cell">Effective monthly</th>
                      <th scope="col" className="amount-cell">Effective annual</th>
                      <th scope="col">Status</th>
                      <th scope="col">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {group.map((item) => (
                      <tr key={item.id}>
                        <td>
                          <div className="merchant-cell">
                            <span
                              className={`merchant-avatar sub-service-badge sub-service-badge--${item.kind}`}
                              aria-hidden="true"
                            >
                              <ServiceIcon kind={item.kind} />
                            </span>
                            <div className="description">
                              <span><strong>{item.name}</strong></span>
                              {item.notes && <small>{item.notes}</small>}
                            </div>
                          </div>
                        </td>
                        <td>
                          {item.subcategory_name ? (
                            <span>
                              {item.subcategory_name}
                              {item.budget_bucket && <small className="bucket-label">{bucketLabels[item.budget_bucket]}</small>}
                            </span>
                          ) : <span className="muted">—</span>}
                        </td>
                        <td>
                          {kindKey === 'ANNUAL_SUBSCRIPTION'
                            ? (item.renewal_date || '—')
                            : (item.due_day ? `Day ${item.due_day}` : '—')}
                        </td>
                        <td>{item.auto_pay ? 'Yes' : 'No'}</td>
                        <td className="amount-cell">{formatAmount(item.amount)}</td>
                        <td className="amount-cell">{formatAmount(item.effective_monthly_cost)} / mo</td>
                        <td className="amount-cell">{formatAmount(item.effective_annual_cost)} / yr</td>
                        <td>
                          <span className={`category-state ${item.is_active ? '' : 'category-state--archived'}`}>
                            {item.is_active ? 'Active' : 'Archived'}
                          </span>
                        </td>
                        <td className="row-actions">
                          <button type="button" onClick={() => beginEdit(item)}>Edit</button>
                          <button type="button" className="quiet-button" onClick={() => void toggleActive(item)}>
                            {item.is_active ? 'Archive' : 'Reactivate'}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )
        })}

        {visible.length === 0 && (
          <p className="message">No recurring bills or subscriptions tracked yet.</p>
        )}
      </section>
    </div>
  )
}

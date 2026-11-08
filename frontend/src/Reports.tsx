import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { apiFetch } from './api'

type SummaryValue = { amount: string; transaction_count: number }
type AllocationValue = SummaryValue & {
  target_percent: string
  actual_percent: string | null
  variance_points: string | null
  target_amount: string | null
}
type BreakdownRow = {
  subcategory: number
  subcategory_name: string
  amount: string
  transaction_count: number
}
type Report = {
  period: { year: number; month: number; start_date: string; end_date: string }
  summary: {
    income: SummaryValue
    needs: AllocationValue
    wants: AllocationValue
    savings_contributions: AllocationValue
    savings_withdrawals: SummaryValue
    net_savings_activity: SummaryValue
    remaining: { amount: string }
  }
  breakdown: Record<'NEEDS' | 'WANTS' | 'SAVINGS', BreakdownRow[]>
  data_completeness: {
    is_incomplete: boolean
    affected_transaction_count: number
    blockers: Record<string, { transaction_count: number }>
    excluded: Record<string, number>
  }
}
type Targets = { needs_percent: string; wants_percent: string; savings_percent: string }
type DrillRow = {
  id: number
  date: string
  description: string
  raw_description: string
  account: string
  classification: string
  savings_effect: string
  subcategory: string | null
  amount: string
  contribution: string
}
type Drilldown = { count: number; next: number | null; previous: number | null; results: DrillRow[] }
type DrillSelection = { metric: string; title: string; subcategory?: number }

const blockerLabels: Record<string, string> = {
  UNKNOWN: 'Unknown classification',
  UNCATEGORIZED: 'Uncategorized Expense or Refund',
  RULE_CONFLICT: 'Categorization-rule conflict',
  UNRESOLVED_DUPLICATE: 'Unresolved possible duplicate',
  TRANSFER_SAVINGS_UNREVIEWED: 'Transfer Savings effect not reviewed',
  SAVINGS_EFFECT_INVALID: 'Invalid Savings effect',
}
const bucketLabels = { NEEDS: 'Needs', WANTS: 'Wants', SAVINGS: 'Savings' } as const

function formatAmount(value: string) {
  const negative = value.startsWith('-')
  const [whole, fraction = '00'] = (negative ? value.slice(1) : value).split('.')
  return `${negative ? '-' : ''}$${whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',')}.${fraction.padEnd(2, '0')}`
}

function amountInCents(value: string) {
  const negative = value.startsWith('-')
  const [whole, fraction = '00'] = (negative ? value.slice(1) : value).split('.')
  const cents = BigInt(whole) * 100n + BigInt(fraction.padEnd(2, '0').slice(0, 2))
  return negative ? -cents : cents
}

function formatCents(value: bigint) {
  const absolute = value < 0n ? -value : value
  const formatted = `${value < 0n ? '-' : ''}${absolute / 100n}.${String(absolute % 100n).padStart(2, '0')}`
  return formatAmount(formatted)
}

function shiftMonth(value: string, delta: number) {
  const [year, month] = value.split('-').map(Number)
  const next = new Date(Date.UTC(year, month - 1 + delta, 1))
  return `${next.getUTCFullYear()}-${String(next.getUTCMonth() + 1).padStart(2, '0')}`
}

function formatMonth(year: number, month: number) {
  return new Intl.DateTimeFormat(undefined, { month: 'long', year: 'numeric', timeZone: 'UTC' })
    .format(new Date(Date.UTC(year, month - 1, 1)))
}

export default function Reports({ reload }: { reload: number }) {
  const [month, setMonth] = useState('')
  const [report, setReport] = useState<Report | null>(null)
  const [targets, setTargets] = useState<Targets | null>(null)
  const [targetDraft, setTargetDraft] = useState<Targets | null>(null)
  const [drillSelection, setDrillSelection] = useState<DrillSelection | null>(null)
  const [drilldown, setDrilldown] = useState<Drilldown | null>(null)
  const [drillPage, setDrillPage] = useState(1)
  const [reportRefresh, setReportRefresh] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  useEffect(() => {
    apiFetch('/api/budget-targets/').then(async (response) => {
      const result = await response.json()
      if (!response.ok) throw new Error('Unable to load budget targets.')
      setTargets(result as Targets)
      setTargetDraft(result as Targets)
    }).catch((failure: unknown) => setError(failure instanceof Error ? failure.message : 'Unable to load budget targets.'))
  }, [])

  useEffect(() => {
    let active = true
    const params = new URLSearchParams()
    if (month) {
      const [year, monthNumber] = month.split('-')
      params.set('year', year)
      params.set('month', String(Number(monthNumber)))
    }
    setLoading(true)
    setError('')
    apiFetch(`/api/reports/monthly/?${params.toString()}`)
      .then(async (response) => {
        const result = await response.json()
        if (!response.ok) throw new Error('Unable to load the monthly report.')
        if (active) {
          const next = result as Report
          setReport(next)
          setMonth(`${next.period.year}-${String(next.period.month).padStart(2, '0')}`)
        }
      })
      .catch((failure: unknown) => {
        if (active) setError(failure instanceof Error ? failure.message : 'Unable to load the monthly report.')
      })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [month, reload, reportRefresh])

  useEffect(() => {
    if (!drillSelection || !report) return
    let active = true
    const [year, monthNumber] = month.split('-')
    const params = new URLSearchParams({
      year,
      month: String(Number(monthNumber)),
      metric: drillSelection.metric,
      page: String(drillPage),
    })
    if (drillSelection.subcategory) params.set('subcategory', String(drillSelection.subcategory))
    apiFetch(`/api/reports/monthly/drilldown/?${params}`)
      .then(async (response) => {
        const result = await response.json()
        if (!response.ok) throw new Error('Unable to load report transactions.')
        if (active) setDrilldown(result as Drilldown)
      })
      .catch((failure: unknown) => {
        if (active) setError(failure instanceof Error ? failure.message : 'Unable to load report transactions.')
      })
    return () => { active = false }
  }, [drillSelection, drillPage, report, month])

  async function saveTargets(event: FormEvent) {
    event.preventDefault()
    if (!targetDraft) return
    setError('')
    setNotice('')
    try {
      const response = await apiFetch('/api/budget-targets/', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(targetDraft),
      })
      const result = await response.json()
      if (!response.ok) throw new Error('Targets must be between 0 and 100.')
      setTargets(result as Targets)
      setTargetDraft(result as Targets)
      setNotice('Budget targets saved. Comparisons use these targets for every month.')
      setReportRefresh((current) => current + 1)
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Unable to save targets.')
    }
  }

  function openDrill(metric: string, title: string, subcategory?: number) {
    setDrillSelection({ metric, title, subcategory })
    setDrillPage(1)
    setDrilldown(null)
  }

  function renderAllocation(label: string, metric: string, value: AllocationValue) {
    return (
      <tr key={metric}>
        <th scope="row"><button className="report-link" type="button" onClick={() => openDrill(metric, label)}>{label}</button></th>
        <td className="amount-cell">{formatAmount(value.amount)}</td>
        <td>{value.actual_percent === null ? 'N/A' : `${value.actual_percent}%`}</td>
        <td>{value.target_percent}%{value.target_amount === null ? '' : ` · ${formatAmount(value.target_amount)}`}</td>
        <td>{value.variance_points === null ? 'N/A' : `${!value.variance_points.startsWith('-') && value.variance_points !== '0.00' ? '+' : ''}${value.variance_points} pts`}</td>
        <td>{value.transaction_count}</td>
      </tr>
    )
  }

  function renderAllocationChart(current: Report) {
    const components = [
      { key: 'needs', label: 'Needs', amount: amountInCents(current.summary.needs.amount), metric: 'needs', color: 'var(--chart-needs)' },
      { key: 'wants', label: 'Wants', amount: amountInCents(current.summary.wants.amount), metric: 'wants', color: 'var(--chart-wants)' },
      { key: 'savings', label: 'Savings contributions', amount: amountInCents(current.summary.savings_contributions.amount), metric: 'savings_contributions', color: 'var(--chart-savings)' },
    ]
    const income = amountInCents(current.summary.income.amount)
    const remaining = amountInCents(current.summary.remaining.amount)
    const hasNegativeComponent = components.some((component) => component.amount < 0n)
    const mixTotal = components.reduce((total, component) => total + component.amount, 0n)
    const mode = remaining >= 0n ? 'income' : 'mix'
    const total = mode === 'income' ? income : mixTotal
    const slices = mode === 'income'
      ? [...components, {
        key: 'remaining',
        label: 'Remaining / Unallocated',
        amount: remaining,
        metric: 'remaining',
        color: 'var(--chart-remaining)',
      }]
      : components

    let unavailableMessage = ''
    if (hasNegativeComponent) {
      unavailableMessage = 'A negative Needs or Wants total cannot be represented in a donut chart.'
    } else if (income <= 0n) {
      unavailableMessage = 'Reported Income must be positive to chart allocations.'
    } else if (total <= 0n) {
      unavailableMessage = 'There are no positive allocations to chart.'
    }

    const circumference = 2 * Math.PI * 56
    let offset = 0
    const chartTitle = mode === 'income' ? 'Income allocation' : 'Share of allocations'
    const centerAmount = formatCents(mode === 'income' ? income : mixTotal)

    return (
      <section className="report-allocation-chart" aria-label="Monthly allocation chart">
        <h3>{chartTitle}</h3>
        {current.data_completeness.is_incomplete && (
          <p className="report-chart-partial">
            Partial data — {current.data_completeness.affected_transaction_count} transactions still require attention.
          </p>
        )}
        {mode === 'mix' && income > 0n && (
          <p className="report-chart-warning">
            Allocations exceed reported Income by {formatCents(-remaining)}.
          </p>
        )}
        {unavailableMessage ? (
          <p className="report-chart-unavailable">{unavailableMessage}</p>
        ) : (
          <>
            <div className="report-chart-content">
              <svg className="allocation-donut" viewBox="0 0 160 160" aria-label={chartTitle}>
                <circle className="allocation-donut-track" cx="80" cy="80" r="56" />
                {slices.filter((slice) => slice.amount > 0n).map((slice) => {
                  const ratio = Number(slice.amount) / Number(total)
                  const length = ratio * circumference
                  const segmentOffset = offset
                  offset += length
                  const shareLabel = `${(ratio * 100).toFixed(1)}% ${mode === 'income' ? 'of reported Income' : 'of positive allocations'}`
                  return (
                    <circle
                      key={slice.key}
                      className="allocation-donut-slice"
                      cx="80"
                      cy="80"
                      r="56"
                      stroke={slice.color}
                      strokeDasharray={`${length} ${circumference - length}`}
                      strokeDashoffset={-segmentOffset}
                      role="button"
                      tabIndex={0}
                      aria-label={`${slice.label}: ${formatCents(slice.amount)}, ${shareLabel}. Open transactions.`}
                      onClick={() => openDrill(slice.metric, slice.label)}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter' || event.key === ' ') {
                          event.preventDefault()
                          openDrill(slice.metric, slice.label)
                        }
                      }}
                    >
                      <title>{slice.label}: {formatCents(slice.amount)} ({shareLabel})</title>
                    </circle>
                  )
                })}
                <text className="allocation-donut-total" x="80" y="76" textAnchor="middle">{centerAmount}</text>
                <text className="allocation-donut-caption" x="80" y="94" textAnchor="middle">
                  {mode === 'income' ? 'reported Income' : 'allocated'}
                </text>
              </svg>
              <div className="allocation-donut-legend">
                {slices.map((slice) => {
                  const ratio = total > 0n ? Number(slice.amount) / Number(total) : 0
                  return (
                    <button
                      key={slice.key}
                      type="button"
                      className="allocation-legend-item"
                      onClick={() => openDrill(slice.metric, slice.label)}
                    >
                      <span className="allocation-legend-swatch" style={{ backgroundColor: slice.color }} />
                      <span>{slice.label}</span>
                      <strong>{formatCents(slice.amount)}</strong>
                      <small>{(ratio * 100).toFixed(1)}%</small>
                    </button>
                  )
                })}
              </div>
            </div>
            <p className="report-chart-caption">
              {mode === 'income'
                ? 'Shares of reported Income. Select a segment or label to inspect its transactions.'
                : 'Shares of positive allocations, not Income. Select a segment or label to inspect its transactions.'}
            </p>
          </>
        )}
      </section>
    )
  }

  return (
    <section className="reports-panel" aria-label="Monthly report">
      <div className="report-toolbar">
        <div className="month-navigation">
          <button type="button" disabled={!month} onClick={() => setMonth(shiftMonth(month, -1))} aria-label="Previous month">‹</button>
          <label>
            Report month
            <input type="month" value={month} onChange={(event) => {
              setDrillSelection(null)
              setMonth(event.target.value)
            }} />
          </label>
          <button type="button" disabled={!month} onClick={() => setMonth(shiftMonth(month, 1))} aria-label="Next month">›</button>
        </div>
        {loading && <span className="muted">Loading…</span>}
      </div>
      {error && <p className="message message--error" role="alert">{error}</p>}
      {notice && <p className="table-notice" role="status">{notice}</p>}
      {report && (
        <>
          {report.data_completeness.is_incomplete && (
            <aside className="report-completeness" role="status">
              <strong>{formatMonth(report.period.year, report.period.month)} has {report.data_completeness.affected_transaction_count} transactions still requiring attention.</strong>
              <span>Some totals may be incomplete.</span>
              <ul>
                {Object.entries(report.data_completeness.blockers).map(([code, value]) => (
                  <li key={code}>{blockerLabels[code] || code}: {value.transaction_count}</li>
                ))}
              </ul>
            </aside>
          )}
          <div className="report-summary-layout">
            <div className="report-summary">
              <table>
                <thead><tr><th>Monthly allocation</th><th>Actual</th><th>Actual % of income</th><th>Target</th><th>Variance</th><th>Transactions</th></tr></thead>
                <tbody>
                  <tr>
                    <th><button className="report-link" type="button" onClick={() => openDrill('income', 'Income')}>Income</button></th>
                    <td className="amount-cell">{formatAmount(report.summary.income.amount)}</td>
                    <td>—</td><td>—</td><td>—</td><td>{report.summary.income.transaction_count}</td>
                  </tr>
                  {renderAllocation('Needs', 'needs', report.summary.needs)}
                  {renderAllocation('Wants', 'wants', report.summary.wants)}
                  {renderAllocation('Savings contributions', 'savings_contributions', report.summary.savings_contributions)}
                  <tr>
                    <th><button className="report-link" type="button" onClick={() => openDrill('savings_withdrawals', 'Savings withdrawals')}>Savings withdrawals</button></th>
                    <td className="amount-cell">{formatAmount(report.summary.savings_withdrawals.amount)}</td><td>—</td><td>—</td><td>—</td>
                    <td>{report.summary.savings_withdrawals.transaction_count}</td>
                  </tr>
                  <tr>
                    <th><button className="report-link" type="button" onClick={() => openDrill('net_savings_activity', 'Net Savings activity')}>Net Savings activity</button></th>
                    <td className="amount-cell">{formatAmount(report.summary.net_savings_activity.amount)}</td><td>—</td><td>—</td><td>—</td>
                    <td>{report.summary.net_savings_activity.transaction_count}</td>
                  </tr>
                  <tr className="remaining-row">
                    <th><button className="report-link" type="button" onClick={() => openDrill('remaining', 'Remaining / Unallocated reconciliation')}>Remaining / Unallocated</button></th>
                    <td className="amount-cell">{formatAmount(report.summary.remaining.amount)}</td><td colSpan={4}>Income less Needs, Wants, and Savings contributions</td>
                  </tr>
                </tbody>
              </table>
            </div>
            {renderAllocationChart(report)}
          </div>

          <section className="report-target-editor">
            <h3>Allocation targets</h3>
            <p>Current guideposts apply to every month; changing them does not alter transaction history.</p>
            {targetDraft && (
              <form onSubmit={(event) => void saveTargets(event)}>
                {(['needs_percent', 'wants_percent', 'savings_percent'] as const).map((field) => (
                  <label key={field}>
                    {field.replace('_percent', '').replace(/^./, (letter) => letter.toUpperCase())} %
                    <input type="number" min="0" max="100" step="0.01" value={targetDraft[field]}
                      onChange={(event) => setTargetDraft({ ...targetDraft, [field]: event.target.value })} />
                  </label>
                ))}
                <button className="secondary-button" type="submit">Save targets</button>
                {targets && <span className="muted">Saved: {targets.needs_percent}/{targets.wants_percent}/{targets.savings_percent}%</span>}
              </form>
            )}
          </section>

          <div className="report-breakdowns">
            {(['NEEDS', 'WANTS', 'SAVINGS'] as const).map((bucket) => (
              <section key={bucket}>
                <h3>{bucketLabels[bucket]} subcategories</h3>
                {report.breakdown[bucket].length ? (
                  <table>
                    <thead><tr><th>Subcategory</th><th>Net</th><th>Transactions</th></tr></thead>
                    <tbody>{report.breakdown[bucket].map((row) => (
                      <tr key={row.subcategory}>
                        <th><button className="report-link" type="button" onClick={() => openDrill(bucket === 'SAVINGS' ? 'net_savings_activity' : bucket.toLowerCase(), `${row.subcategory_name} · ${bucketLabels[bucket]}`, row.subcategory)}>{row.subcategory_name}</button></th>
                        <td className="amount-cell">{formatAmount(row.amount)}</td><td>{row.transaction_count}</td>
                      </tr>
                    ))}</tbody>
                  </table>
                ) : <p className="muted">No eligible transactions.</p>}
              </section>
            ))}
          </div>

          {drillSelection && (
            <section className="report-drilldown" aria-label="Report transaction drilldown">
              <header>
                <h3>{drillSelection.title}</h3>
                <button className="quiet-button" type="button" onClick={() => {
                  setDrillSelection(null)
                  setDrilldown(null)
                }}>Close</button>
              </header>
              <p>Contribution shows how each transaction affects the selected report amount. Amount is the original signed ledger value.</p>
              {drilldown && (
                <>
                  <div className="table-scroll"><table>
                    <thead><tr><th>Date</th><th>Description</th><th>Account</th><th>Type</th><th>Subcategory</th><th>Ledger amount</th><th>Report contribution</th></tr></thead>
                    <tbody>{drilldown.results.map((row) => (
                      <tr key={row.id}><td>{row.date}</td><td>{row.description}</td><td>{row.account}</td><td>{row.classification}{row.savings_effect !== 'NONE' ? ` · ${row.savings_effect}` : ''}</td><td>{row.subcategory || '—'}</td><td className="amount-cell">{formatAmount(row.amount)}</td><td className="amount-cell">{formatAmount(row.contribution)}</td></tr>
                    ))}</tbody>
                  </table></div>
                  <div className="report-pagination">
                    <span>{drilldown.count} transactions</span>
                    <button type="button" disabled={!drilldown.previous} onClick={() => setDrillPage(drillPage - 1)}>Previous</button>
                    <button type="button" disabled={!drilldown.next} onClick={() => setDrillPage(drillPage + 1)}>Next</button>
                  </div>
                </>
              )}
            </section>
          )}
        </>
      )}
    </section>
  )
}

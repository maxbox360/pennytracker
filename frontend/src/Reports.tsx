import { useEffect, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { apiFetch } from './api'

type SummaryValue = { amount: string; transaction_count: number }
type AllocationValue = SummaryValue & {
  target_percent: string
  actual_percent: string | null
  variance_points: string | null
  target_amount: string | null
}
type Summary = {
  income: SummaryValue
  other_inflows: SummaryValue
  needs: AllocationValue
  wants: AllocationValue
  savings_contributions: AllocationValue
  savings_withdrawals: SummaryValue
  net_savings_activity: SummaryValue
  remaining: { amount: string }
}
type BreakdownRow = {
  subcategory: number | null
  subcategory_name: string
  amount: string
  transaction_count: number
}
type Completeness = {
  is_incomplete: boolean
  affected_transaction_count: number
  blockers: Record<string, { transaction_count: number }>
  excluded: Record<string, number>
}
type PeriodRow = {
  period: { year: number; month: number; start_date: string; end_date: string }
  summary: Summary
  data_completeness: Completeness
  ledger_transaction_count: number
}
type TrendPoint = {
  year: number
  month: number
  label: string
  has_transactions: boolean
  is_incomplete: boolean
  affected_transaction_count: number
  income: string
  other_inflows: string
  needs: string
  wants: string
  savings_contributions: string
  savings_withdrawals: string
  net_savings_activity: string
  remaining: string
  needs_percent: string | null
  wants_percent: string | null
  savings_percent: string | null
  current_targets: {
    needs_percent: string
    wants_percent: string
    savings_percent: string
  }
}
type ReportBase = {
  summary: Summary
  data_completeness: Completeness
  breakdown: Record<'NEEDS' | 'WANTS' | 'SAVINGS', BreakdownRow[]>
  other_inflows_breakdown: BreakdownRow[]
}
type HistoricalReport = ReportBase & {
  monthly_series: TrendPoint[]
}
type MonthlyReport = ReportBase & {
  period: PeriodRow['period']
}
type YearReport = HistoricalReport & {
  period: { year: number; start_date: string; end_date: string }
  months: PeriodRow[]
}
type AllTimeReport = HistoricalReport & {
  period: { start_date: string | null; end_date: string | null }
  years: {
    year: number
    summary: Summary
    data_completeness: Completeness
    ledger_transaction_count: number
  }[]
  months: PeriodRow[]
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
type DrillSelection = { metric: string; title: string; subcategory?: number | 'uncategorized' }
type CategoryTrend = {
  subcategory: number
  subcategory_name: string
  scope: { type: 'all' | 'year' | 'month'; year?: number; month?: number }
  series: {
    year: number
    month: number
    label: string
    is_incomplete: boolean
    affected_transaction_count: number
    buckets: Record<'NEEDS' | 'WANTS', { amount: string; transaction_count: number }>
  }[]
}
type ChartLine = { key: string; label: string; color: string; target?: string }
type ChartPoint = {
  label: string
  empty: boolean
  emptyLabel?: string
  incomplete: boolean
  affected_transaction_count: number
  values: Record<string, string | null>
}

const blockerLabels: Record<string, string> = {
  UNKNOWN: 'Unknown classification',
  UNCATEGORIZED: 'Uncategorized Expense or Refund',
  RULE_CONFLICT: 'Categorization-rule conflict',
  UNRESOLVED_DUPLICATE: 'Unresolved possible duplicate',
  TRANSFER_SAVINGS_UNREVIEWED: 'Transfer Savings effect not reviewed',
  SAVINGS_EFFECT_INVALID: 'Invalid Savings effect',
}
const bucketLabels = { NEEDS: 'Needs', WANTS: 'Wants', SAVINGS: 'Savings' } as const
const metricLabels: Record<keyof Summary, string> = {
  income: 'Income',
  other_inflows: 'Other Inflows',
  needs: 'Needs',
  wants: 'Wants',
  savings_contributions: 'Savings Contributions',
  savings_withdrawals: 'Savings Withdrawals',
  net_savings_activity: 'Net Savings Activity',
  remaining: 'Remaining / Unallocated',
}
const dollarLines: ChartLine[] = [
  { key: 'income', label: 'Income', color: 'var(--chart-income)' },
  { key: 'needs', label: 'Needs', color: 'var(--chart-needs)' },
  { key: 'wants', label: 'Wants', color: 'var(--chart-wants)' },
  { key: 'savings_contributions', label: 'Savings Contributions', color: 'var(--chart-savings)' },
]
const dollarTableLines: ChartLine[] = [
  ...dollarLines,
  { key: 'savings_withdrawals', label: 'Savings Withdrawals', color: 'var(--chart-remaining)' },
  { key: 'net_savings_activity', label: 'Net Savings Activity', color: 'var(--chart-savings)' },
  { key: 'other_inflows', label: 'Other Inflows', color: 'var(--chart-remaining)' },
  { key: 'remaining', label: 'Remaining / Unallocated', color: 'var(--muted)' },
]
const percentageLines: ChartLine[] = [
  { key: 'needs_percent', label: 'Needs %', color: 'var(--chart-needs)', target: 'needs_target' },
  { key: 'wants_percent', label: 'Wants %', color: 'var(--chart-wants)', target: 'wants_target' },
  { key: 'savings_percent', label: 'Savings Contribution %', color: 'var(--chart-savings)', target: 'savings_target' },
]

function formatAmount(value: string) {
  const negative = value.startsWith('-')
  const [whole, fraction = '00'] = (negative ? value.slice(1) : value).split('.')
  return `${negative ? '-' : ''}$${whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',')}.${fraction.padEnd(2, '0')}`
}

function formatMonth(year: number, month: number) {
  return new Intl.DateTimeFormat(undefined, { month: 'long', year: 'numeric', timeZone: 'UTC' })
    .format(new Date(Date.UTC(year, month - 1, 1)))
}

function periodStatus(completeness: Completeness, transactionCount: number, summary: Summary) {
  if (completeness.is_incomplete) return `Incomplete · ${completeness.affected_transaction_count}`
  if (!transactionCount) return 'No transactions'
  const amounts = [
    summary.income.amount,
    summary.other_inflows.amount,
    summary.needs.amount,
    summary.wants.amount,
    summary.savings_contributions.amount,
    summary.savings_withdrawals.amount,
  ]
  return amounts.every((amount) => amountInCents(amount) === 0n) ? 'Known zero activity' : 'Complete'
}

function SummaryTable({
  summary,
  openDrill,
}: {
  summary: Summary
  openDrill: (metric: string, title: string) => void
}) {
  const values: (keyof Summary)[] = [
    'income',
    'other_inflows',
    'needs',
    'wants',
    'savings_contributions',
    'savings_withdrawals',
    'net_savings_activity',
    'remaining',
  ]
  return (
    <div className="historical-summary report-summary">
      <table>
        <thead><tr><th>Accounting line</th><th>Amount</th><th>% of Income</th><th>Current target</th><th>Variance</th><th>Transactions</th></tr></thead>
        <tbody>
          {values.map((metric) => {
            const value = summary[metric]
            const allocation = ['needs', 'wants', 'savings_contributions'].includes(metric)
              ? value as AllocationValue
              : null
            const amount = typeof value.amount === 'string' ? value.amount : '0.00'
            return (
              <tr key={metric} className={metric === 'remaining' ? 'remaining-row' : undefined}>
                <th scope="row"><button className="report-link" type="button" onClick={() => openDrill(metric, metricLabels[metric])}>{metricLabels[metric]}</button></th>
                <td className="amount-cell">{formatAmount(amount)}</td>
                <td>{allocation?.actual_percent === null || !allocation ? '—' : `${allocation.actual_percent}%`}</td>
                <td>{allocation ? `${allocation.target_percent}%${allocation.target_amount === null ? '' : ` · ${formatAmount(allocation.target_amount)}`}` : '—'}</td>
                <td>{allocation?.variance_points === null || !allocation ? '—' : `${!allocation.variance_points.startsWith('-') && allocation.variance_points !== '0.00' ? '+' : ''}${allocation.variance_points} pts`}</td>
                <td>{'transaction_count' in value ? value.transaction_count : '—'}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

const blockerReviewReasons: Record<string, string> = {
  UNKNOWN: 'UNKNOWN',
  UNCATEGORIZED: 'UNCATEGORIZED',
  RULE_CONFLICT: 'RULE_CONFLICT',
  UNRESOLVED_DUPLICATE: 'POSSIBLE_DUPLICATE',
  TRANSFER_SAVINGS_UNREVIEWED: 'TRANSFER',
}

function CompletenessNotice({
  completeness,
  onReview,
}: {
  completeness: Completeness
  onReview: (reason: string) => void
}) {
  if (!completeness.is_incomplete) return null
  return (
    <aside className="report-completeness" role="status">
      <strong>{completeness.affected_transaction_count} transactions still require attention.</strong>
      <span>Reported totals may be incomplete.</span>
      <ul>
        {Object.entries(completeness.blockers).map(([code, value]) => (
          <li key={code}>
            {blockerLabels[code] || code}: {value.transaction_count}
            {blockerReviewReasons[code] && (
              <> · <button type="button" className="link-button" onClick={() => onReview(blockerReviewReasons[code])}>Review</button></>
            )}
          </li>
        ))}
      </ul>
    </aside>
  )
}

function Breakdown({
  breakdown,
  otherInflows,
  onSelect,
}: {
  breakdown: ReportBase['breakdown']
  otherInflows: ReportBase['other_inflows_breakdown']
  onSelect: (metric: string, title: string, subcategory: number | null) => void
}) {
  return (
    <div className="report-breakdowns">
      {(['NEEDS', 'WANTS', 'SAVINGS'] as const).map((bucket) => (
        <section key={bucket}>
          <h3>{bucketLabels[bucket]} subcategories</h3>
          {breakdown[bucket].length ? (
            <table>
              <thead><tr><th>Subcategory</th><th>Net</th><th>Transactions</th></tr></thead>
              <tbody>{breakdown[bucket].map((row) => (
                <tr key={`${row.subcategory}-${bucket}`}>
                  <th><button
                    className="report-link"
                    type="button"
                    onClick={() => onSelect(
                      bucket === 'SAVINGS' ? 'net_savings_activity' : bucket.toLowerCase(),
                      `${row.subcategory_name} · ${bucketLabels[bucket]}`,
                      row.subcategory,
                    )}
                  >{row.subcategory_name}</button></th>
                  <td className="amount-cell">{formatAmount(row.amount)}</td>
                  <td>{row.transaction_count}</td>
                </tr>
              ))}</tbody>
            </table>
          ) : <p className="muted">No eligible transactions.</p>}
        </section>
      ))}
      <section>
        <h3>Other Inflows</h3>
        {otherInflows.length ? (
          <table>
            <thead><tr><th>Subcategory</th><th>Amount</th><th>Transactions</th></tr></thead>
            <tbody>{otherInflows.map((row) => (
              <tr key={row.subcategory ?? 'uncategorized'}>
                <th><button
                  className="report-link"
                  type="button"
                  onClick={() => onSelect('other_inflows', `${row.subcategory_name} · Other Inflows`, row.subcategory)}
                >{row.subcategory_name}</button></th>
                <td className="amount-cell">{formatAmount(row.amount)}</td>
                <td>{row.transaction_count}</td>
              </tr>
            ))}</tbody>
          </table>
        ) : <p className="muted">No eligible transactions.</p>}
      </section>
    </div>
  )
}

function TrendChart({
  title,
  description,
  points,
  lines,
  tableLines = lines,
  percentage = false,
}: {
  title: string
  description: string
  points: ChartPoint[]
  lines: ChartLine[]
  tableLines?: ChartLine[]
  percentage?: boolean
}) {
  const width = Math.max(760, points.length * 38)
  const height = 220
  const left = 54
  const right = 18
  const top = 18
  const bottom = 32
  const chartHeight = height - top - bottom
  const chartWidth = width - left - right
  const values = points.flatMap((point) => lines
    .map((line) => point.values[line.key])
    .filter((value): value is string => value !== null)
    .map(Number))
  const targets = percentage
    ? lines.flatMap((line) => {
      if (!line.target) return []
      const target = line.target
      return points.map((point) => point.values[target] ?? '').filter(Boolean).map(Number)
    })
    : []
  const combined = [...values, ...targets]
  const minimum = combined.length ? Math.min(0, ...combined) : 0
  const maximum = combined.length ? Math.max(1, ...combined) : 1
  const padding = Math.max((maximum - minimum) * 0.1, 1)
  const low = minimum - padding
  const high = maximum + padding
  const x = (index: number) => left + (points.length <= 1 ? chartWidth / 2 : index * chartWidth / (points.length - 1))
  const y = (value: string | number) => top + (high - Number(value)) * chartHeight / (high - low)
  const linePaths = lines.map((line) => {
    let active = false
    const path = points.map((point, index) => {
      const value = point.values[line.key]
      if (value === null || value === undefined) {
        active = false
        return ''
      }
      const command = `${active ? 'L' : 'M'} ${x(index)} ${y(value)}`
      active = true
      return command
    }).filter(Boolean).join(' ')
    return { ...line, path }
  })

  if (!points.length) return <section className="historical-chart"><h3>{title}</h3><p className="muted">No transaction history to chart.</p></section>
  return (
    <section className="historical-chart">
      <h3>{title}</h3>
      <p>{description}</p>
      <div className="historical-chart-scroll">
        <svg
          className="historical-chart-svg"
          width={width}
          height={height}
          viewBox={`0 0 ${width} ${height}`}
          role="img"
          aria-label={`${title}. Values are also available in the table below.`}
        >
          <title>{title}</title>
          {[0, 0.5, 1].map((fraction) => {
            const value = high - fraction * (high - low)
            const yPosition = top + fraction * chartHeight
            return (
              <g key={fraction}>
                <line className="trend-gridline" x1={left} x2={width - right} y1={yPosition} y2={yPosition} />
                <text className="trend-axis-label" x={left - 7} y={yPosition + 4} textAnchor="end">
                  {percentage ? `${value.toFixed(0)}%` : `$${value.toFixed(0)}`}
                </text>
              </g>
            )
          })}
          {percentage && lines.filter((line) => line.target && points[0].values[line.target] !== undefined).map((line) => (
            <g key={line.target}>
              <line
                className="trend-target-line"
                x1={left}
                x2={width - right}
                y1={y(points[0].values[line.target!] || '0')}
                y2={y(points[0].values[line.target!] || '0')}
                stroke={line.color}
              />
              <text className="trend-target-label" x={width - right - 2} y={y(points[0].values[line.target!] || '0') - 4} textAnchor="end">
                Current target
              </text>
            </g>
          ))}
          {linePaths.map((line) => (
            <path key={line.key} className="trend-series-line" d={line.path} stroke={line.color}>
              <title>{line.label}</title>
            </path>
          ))}
          {points.map((point, index) => (
            <g key={point.label}>
              {lines.map((line) => {
                const value = point.values[line.key]
                if (value === null || value === undefined) return null
                return (
                  <circle
                    key={line.key}
                    className={`trend-point ${point.incomplete ? 'trend-point--incomplete' : ''}`}
                    cx={x(index)}
                    cy={y(value)}
                    r={point.incomplete ? 4 : 3}
                    fill={line.color}
                  >
                    <title>{`${point.label} · ${line.label}: ${percentage ? `${value}%` : formatAmount(value)}${point.incomplete ? ' · incomplete' : ''}`}</title>
                  </circle>
                )
              })}
              <text className="trend-axis-label" x={x(index)} y={height - 8} textAnchor="middle">
                {point.label.slice(2)}
              </text>
            </g>
          ))}
        </svg>
      </div>
      <div className="trend-legend">
        {lines.map((line) => <span key={line.key}><i style={{ backgroundColor: line.color }} />{line.label}</span>)}
        {percentage && <span><i className="trend-legend-target" />Current target</span>}
        <span><i className="trend-legend-incomplete" />Incomplete period</span>
      </div>
      <div className="historical-series-table">
        <table>
          <thead><tr><th>Month</th>{tableLines.map((line) => <th key={line.key}>{line.label}</th>)}<th>Data</th></tr></thead>
          <tbody>{points.map((point) => {
            const knownZero = tableLines.every((line) => {
              const value = point.values[line.key]
              return value !== null && value !== undefined && amountInCents(value) === 0n
            })
            return (
              <tr key={point.label} className={point.incomplete ? 'historical-row--incomplete' : undefined}>
                <th>{point.label}</th>
                {tableLines.map((line) => {
                  const value = point.values[line.key]
                  return <td key={line.key} className="amount-cell">{value === null || value === undefined ? '—' : percentage ? `${value}%` : formatAmount(value)}</td>
                })}
                <td>{point.incomplete
                  ? `Incomplete · ${point.affected_transaction_count}`
                  : point.empty
                    ? point.emptyLabel || 'No transactions'
                    : knownZero ? 'Known zero activity' : 'Complete'}</td>
              </tr>
            )
          })}</tbody>
        </table>
      </div>
    </section>
  )
}

function summaryPoint(point: TrendPoint): ChartPoint {
  return {
    label: point.label,
    empty: !point.has_transactions,
    incomplete: point.is_incomplete,
    affected_transaction_count: point.affected_transaction_count,
    values: {
      income: point.income,
      other_inflows: point.other_inflows,
      needs: point.needs,
      wants: point.wants,
      savings_contributions: point.savings_contributions,
      savings_withdrawals: point.savings_withdrawals,
      net_savings_activity: point.net_savings_activity,
      needs_percent: point.needs_percent,
      wants_percent: point.wants_percent,
      savings_percent: point.savings_percent,
      needs_target: point.current_targets.needs_percent,
      wants_target: point.current_targets.wants_percent,
      savings_target: point.current_targets.savings_percent,
    },
  }
}

function isMonthlyReport(report: AllTimeReport | YearReport | MonthlyReport): report is MonthlyReport {
  return 'month' in report.period
}

export default function Reports({ reload, onReview }: { reload: number; onReview: (reason: string) => void }) {
  const [level, setLevel] = useState<'all' | 'year' | 'month'>('all')
  const [year, setYear] = useState<number | null>(null)
  const [month, setMonth] = useState<number | null>(null)
  const [report, setReport] = useState<AllTimeReport | YearReport | MonthlyReport | null>(null)
  const [targetDraft, setTargetDraft] = useState<Targets | null>(null)
  const [drillSelection, setDrillSelection] = useState<DrillSelection | null>(null)
  const [drilldown, setDrilldown] = useState<Drilldown | null>(null)
  const [drillPage, setDrillPage] = useState(1)
  const [categoryTrend, setCategoryTrend] = useState<CategoryTrend | null>(null)
  const [categoryTrendId, setCategoryTrendId] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  useEffect(() => {
    apiFetch('/api/budget-targets/').then(async (response) => {
      const result = await response.json()
      if (!response.ok) throw new Error('Unable to load budget targets.')
      setTargetDraft(result as Targets)
    }).catch((failure: unknown) => setError(failure instanceof Error ? failure.message : 'Unable to load budget targets.'))
  }, [])

  useEffect(() => {
    let active = true
    const path = level === 'all'
      ? '/api/reports/all-time/'
      : level === 'year'
        ? `/api/reports/yearly/?year=${year}`
        : `/api/reports/monthly/?year=${year}&month=${month}`
    setLoading(true)
    setError('')
    apiFetch(path)
      .then(async (response) => {
        const result = await response.json()
        if (!response.ok) throw new Error('Unable to load the report.')
        if (!active) return
        setReport(result as AllTimeReport | YearReport | MonthlyReport)
        if (level === 'all') {
          const allTime = result as AllTimeReport
          if (allTime.years.length && year === null) setYear(allTime.years[allTime.years.length - 1].year)
        }
      })
      .catch((failure: unknown) => {
        if (active) setError(failure instanceof Error ? failure.message : 'Unable to load the report.')
      })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [level, year, month, reload])

  useEffect(() => {
    if (!drillSelection || year === null) return
    let active = true
    const params = new URLSearchParams({
      scope: level === 'month' ? 'month' : level,
      year: String(year),
      metric: drillSelection.metric,
      page: String(drillPage),
    })
    if (level === 'month' && month !== null) params.set('month', String(month))
    if (drillSelection.subcategory) params.set('subcategory', String(drillSelection.subcategory))
    apiFetch(`/api/reports/drilldown/?${params}`)
      .then(async (response) => {
        const result = await response.json()
        if (!response.ok) throw new Error('Unable to load report transactions.')
        if (active) setDrilldown(result as Drilldown)
      })
      .catch((failure: unknown) => {
        if (active) setError(failure instanceof Error ? failure.message : 'Unable to load report transactions.')
      })
    return () => { active = false }
  }, [drillSelection, drillPage, level, year, month])

  useEffect(() => {
    if (categoryTrendId === null || year === null) {
      setCategoryTrend(null)
      return
    }
    let active = true
    const params = new URLSearchParams({
      subcategory: String(categoryTrendId),
      scope: level,
    })
    if (level !== 'all') params.set('year', String(year))
    if (level === 'month' && month !== null) params.set('month', String(month))
    apiFetch(`/api/reports/category-trend/?${params}`)
      .then(async (response) => {
        const result = await response.json()
        if (!response.ok) throw new Error('Unable to load Subcategory history.')
        if (active) setCategoryTrend(result as CategoryTrend)
      })
      .catch((failure: unknown) => {
        if (active) setError(failure instanceof Error ? failure.message : 'Unable to load Subcategory history.')
      })
    return () => { active = false }
  }, [categoryTrendId, level, year, month])

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
      setTargetDraft(result as Targets)
      setNotice('Current targets saved. Historical comparisons use these guideposts.')
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Unable to save targets.')
    }
  }

  function openDrill(metric: string, title: string, subcategory?: number | null) {
    setDrillSelection({
      metric,
      title,
      ...(subcategory === null ? { subcategory: 'uncategorized' } : subcategory !== undefined ? { subcategory } : {}),
    })
    setDrillPage(1)
    setDrilldown(null)
  }

  const summary = report?.summary
  const points = useMemo(
    () => report && 'monthly_series' in report
      ? report.monthly_series.map(summaryPoint)
      : [],
    [report],
  )
  const breakdown = report?.breakdown
  const historicalCategoryLines = useMemo(() => {
    if (!categoryTrend) return []
    const hasNeeds = categoryTrend.series.some((point) => point.buckets.NEEDS.transaction_count)
    const hasWants = categoryTrend.series.some((point) => point.buckets.WANTS.transaction_count)
    return [
      ...(hasNeeds ? [{ key: 'NEEDS', label: 'Needs', color: 'var(--chart-needs)' }] : []),
      ...(hasWants ? [{ key: 'WANTS', label: 'Wants', color: 'var(--chart-wants)' }] : []),
    ]
  }, [categoryTrend])
  const categoryPoints = useMemo(() => categoryTrend?.series.map((point) => ({
    label: point.label,
    empty: point.buckets.NEEDS.transaction_count + point.buckets.WANTS.transaction_count === 0,
    emptyLabel: 'No category activity',
    incomplete: point.is_incomplete,
    affected_transaction_count: point.affected_transaction_count,
    values: {
      NEEDS: point.is_incomplete && !point.buckets.NEEDS.transaction_count ? null : point.buckets.NEEDS.amount,
      WANTS: point.is_incomplete && !point.buckets.WANTS.transaction_count ? null : point.buckets.WANTS.amount,
    },
  })) || [], [categoryTrend])

  function selectCategory(metric: string, title: string, id: number | null) {
    setCategoryTrendId(metric === 'net_savings_activity' || metric === 'other_inflows' || id === null ? null : id)
    openDrill(metric, title, id)
  }

  return (
    <section className="reports-panel" aria-label="Historical reports">
      <nav className="report-breadcrumbs" aria-label="Report hierarchy">
        <button type="button" className={level === 'all' ? 'is-current' : ''} onClick={() => {
          setLevel('all')
          setYear(null)
          setMonth(null)
          setDrillSelection(null)
        }}>All Time</button>
        {year !== null && level !== 'all' && <>
          <span aria-hidden="true">/</span>
          <button type="button" className={level === 'year' ? 'is-current' : ''} onClick={() => {
            setLevel('year')
            setMonth(null)
            setDrillSelection(null)
          }}>{year}</button>
        </>}
        {year !== null && level === 'month' && month !== null && <>
          <span aria-hidden="true">/</span>
          <span className="is-current">{formatMonth(year, month)}</span>
        </>}
      </nav>
      <div className="report-toolbar">
        {level === 'year' && year !== null && (
          <label className="report-period-selector">Report year
            <input type="number" min="1" max="9998" value={year} onChange={(event) => {
              const next = Number(event.target.value)
              if (next >= 1 && next <= 9998) setYear(next)
            }} />
          </label>
        )}
        {level === 'month' && year !== null && month !== null && (
          <div className="month-navigation">
            <button type="button" onClick={() => {
              if (month === 1) { setYear(year - 1); setMonth(12) } else setMonth(month - 1)
            }} aria-label="Previous month">‹</button>
            <label>Report month
              <input type="month" value={`${year}-${String(month).padStart(2, '0')}`} onChange={(event) => {
                const [nextYear, nextMonth] = event.target.value.split('-').map(Number)
                setYear(nextYear)
                setMonth(nextMonth)
                setDrillSelection(null)
              }} />
            </label>
            <button type="button" onClick={() => {
              if (month === 12) { setYear(year + 1); setMonth(1) } else setMonth(month + 1)
            }} aria-label="Next month">›</button>
          </div>
        )}
        {loading && <span className="muted">Loading…</span>}
        {level === 'all' && report && 'period' in report && (
          <span className="muted">
            {report.period.start_date && report.period.end_date
              ? `${report.period.start_date} to ${report.period.end_date}`
              : 'No transaction history'}
          </span>
        )}
      </div>
      {error && <p className="message message--error" role="alert">{error}</p>}
      {notice && <p className="table-notice" role="status">{notice}</p>}
      {report && summary && (
        <>
          <CompletenessNotice completeness={report.data_completeness} onReview={onReview} />
          {level === 'month' && isMonthlyReport(report) ? (
            <div className="report-summary-layout">
              <SummaryTable summary={summary} openDrill={openDrill} />
              <section className="report-allocation-chart">
                <MonthlyDetails report={report} openDrill={openDrill} />
              </section>
            </div>
          ) : <SummaryTable summary={summary} openDrill={openDrill} />}

          {level === 'all' && 'years' in report && (
            <section className="historical-table-section">
              <h3>Calendar years</h3>
              <div className="table-scroll"><table>
                <thead><tr><th>Year</th><th>Income</th><th>Other Inflows</th><th>Needs</th><th>Wants</th><th>Savings Contributions</th><th>Savings Withdrawals</th><th>Net Savings</th><th>Remaining</th><th>Data</th></tr></thead>
                <tbody>{report.years.map((item) => (
                  <tr key={item.year} className={item.data_completeness.is_incomplete ? 'historical-row--incomplete' : undefined}>
                    <th><button className="report-link" type="button" onClick={() => {
                      setYear(item.year)
                      setLevel('year')
                      setDrillSelection(null)
                    }}>{item.year}</button></th>
                    <td className="amount-cell">{formatAmount(item.summary.income.amount)}</td>
                    <td className="amount-cell">{formatAmount(item.summary.other_inflows.amount)}</td>
                    <td className="amount-cell">{formatAmount(item.summary.needs.amount)}</td>
                    <td className="amount-cell">{formatAmount(item.summary.wants.amount)}</td>
                    <td className="amount-cell">{formatAmount(item.summary.savings_contributions.amount)}</td>
                    <td className="amount-cell">{formatAmount(item.summary.savings_withdrawals.amount)}</td>
                    <td className="amount-cell">{formatAmount(item.summary.net_savings_activity.amount)}</td>
                    <td className="amount-cell">{formatAmount(item.summary.remaining.amount)}</td>
                    <td>{periodStatus(item.data_completeness, item.ledger_transaction_count, item.summary)}</td>
                  </tr>
                ))}</tbody>
              </table></div>
            </section>
          )}

          {level === 'year' && 'months' in report && 'year' in report.period && (
            <section className="historical-table-section">
              <h3>{report.period.year} calendar months</h3>
              <div className="table-scroll"><table>
                <thead><tr><th>Month</th><th>Income</th><th>Other Inflows</th><th>Needs</th><th>Wants</th><th>Savings Contributions</th><th>Savings Withdrawals</th><th>Net Savings</th><th>Remaining</th><th>Data</th></tr></thead>
                <tbody>{report.months.map((item) => (
                  <tr key={item.period.month} className={item.data_completeness.is_incomplete ? 'historical-row--incomplete' : undefined}>
                    <th><button className="report-link" type="button" onClick={() => {
                      setYear(item.period.year)
                      setMonth(item.period.month)
                      setLevel('month')
                      setDrillSelection(null)
                    }}>{formatMonth(item.period.year, item.period.month)}</button></th>
                    <td className="amount-cell">{formatAmount(item.summary.income.amount)}</td>
                    <td className="amount-cell">{formatAmount(item.summary.other_inflows.amount)}</td>
                    <td className="amount-cell">{formatAmount(item.summary.needs.amount)}</td>
                    <td className="amount-cell">{formatAmount(item.summary.wants.amount)}</td>
                    <td className="amount-cell">{formatAmount(item.summary.savings_contributions.amount)}</td>
                    <td className="amount-cell">{formatAmount(item.summary.savings_withdrawals.amount)}</td>
                    <td className="amount-cell">{formatAmount(item.summary.net_savings_activity.amount)}</td>
                    <td className="amount-cell">{formatAmount(item.summary.remaining.amount)}</td>
                    <td>{periodStatus(item.data_completeness, item.ledger_transaction_count, item.summary)}</td>
                  </tr>
                ))}</tbody>
              </table></div>
            </section>
          )}

          {level === 'month' && isMonthlyReport(report) && (
            <>
              {targetDraft && (
                <section className="report-target-editor">
                  <h3>Current allocation targets</h3>
                  <p>Current guideposts apply to every month and are not historical snapshots.</p>
                  <form onSubmit={(event) => void saveTargets(event)}>
                    {(['needs_percent', 'wants_percent', 'savings_percent'] as const).map((field) => (
                      <label key={field}>{field.replace('_percent', '').replace(/^./, (letter) => letter.toUpperCase())} %
                        <input type="number" min="0" max="100" step="0.01" value={targetDraft[field]}
                          onChange={(event) => setTargetDraft({ ...targetDraft, [field]: event.target.value })} />
                      </label>
                    ))}
                    <button className="secondary-button" type="submit">Save targets</button>
                  </form>
                </section>
              )}
            </>
          )}

          {breakdown && (
            <Breakdown
              breakdown={breakdown}
              otherInflows={report.other_inflows_breakdown}
              onSelect={(metric, title, id) => selectCategory(metric, title, id)}
            />
          )}

          {level !== 'month' && (
            <>
              <TrendChart
                title="Monthly amounts"
                description="Income and allocation amounts by Transaction.date. Remaining is available in the report tables."
                points={points}
                lines={dollarLines}
                tableLines={dollarTableLines}
              />
              <TrendChart
                title="Allocation percentages"
                description="Monthly share of Income. Dashed references show current targets, not historical target settings."
                points={points}
                lines={percentageLines}
                percentage
              />
            </>
          )}

          {categoryTrend && (
            <TrendChart
              title={`${categoryTrend.subcategory_name} · monthly net spending`}
              description="Expense contributions less Refund contributions, grouped by each transaction's historical bucket snapshot."
              points={categoryPoints}
              lines={historicalCategoryLines}
            />
          )}

          {drillSelection && (
            <section className="report-drilldown" aria-label="Report transaction drilldown">
              <header>
                <h3>{drillSelection.title}</h3>
                <button className="quiet-button" type="button" onClick={() => {
                  setDrillSelection(null)
                  setDrilldown(null)
                }}>Close</button>
              </header>
              <p>Contribution shows each transaction's effect on the selected report amount. Amount is the original signed ledger value.</p>
              {drilldown && <>
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
              </>}
            </section>
          )}
        </>
      )}
    </section>
  )
}

function MonthlyDetails({
  report,
  openDrill,
}: {
  report: MonthlyReport
  openDrill: (metric: string, title: string) => void
}) {
  const components = [
    { key: 'needs', label: 'Needs', amount: amountInCents(report.summary.needs.amount), metric: 'needs', color: 'var(--chart-needs)' },
    { key: 'wants', label: 'Wants', amount: amountInCents(report.summary.wants.amount), metric: 'wants', color: 'var(--chart-wants)' },
    { key: 'savings', label: 'Savings contributions', amount: amountInCents(report.summary.savings_contributions.amount), metric: 'savings_contributions', color: 'var(--chart-savings)' },
  ]
  const income = amountInCents(report.summary.income.amount)
  const remaining = amountInCents(report.summary.remaining.amount)
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
  const unavailableMessage = hasNegativeComponent
    ? 'A negative allocation total cannot be represented in a donut chart.'
    : income <= 0n
      ? 'Reported Income must be positive to chart allocations.'
      : total <= 0n
        ? 'There are no positive allocations to chart.'
        : ''
  const circumference = 2 * Math.PI * 56
  let offset = 0
  const chartTitle = mode === 'income' ? 'Income allocation' : 'Share of allocations'
  const centerAmount = formatCents(mode === 'income' ? income : mixTotal)

  return (
    <>
      <h3>{chartTitle}</h3>
      {report.data_completeness.is_incomplete && (
        <p className="report-chart-partial">
          Partial data — {report.data_completeness.affected_transaction_count} transactions still require attention.
        </p>
      )}
      {mode === 'mix' && income > 0n && (
        <p className="report-chart-warning">Allocations exceed reported Income by {formatCents(-remaining)}.</p>
      )}
      {unavailableMessage ? (
        <p className="report-chart-unavailable">{unavailableMessage}</p>
      ) : (
        <>
          <div className="report-chart-content">
            <svg className="allocation-donut" viewBox="0 0 160 160" role="img" aria-label={`${chartTitle}. Values and transaction links are listed below.`}>
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
    </>
  )
}

function amountInCents(amount: string): bigint {
  const [whole, fraction = '00'] = amount.split('.')
  return BigInt(whole) * 100n + BigInt(fraction.padEnd(2, '0').slice(0, 2)) * (whole.startsWith('-') ? -1n : 1n)
}

function formatCents(amount: bigint): string {
  const negative = amount < 0n
  const absolute = negative ? -amount : amount
  const whole = (absolute / 100n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  const fraction = (absolute % 100n).toString().padStart(2, '0')
  return `${negative ? '-' : ''}$${whole}.${fraction}`
}

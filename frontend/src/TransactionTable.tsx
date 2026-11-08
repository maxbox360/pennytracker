import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { apiFetch } from './api'

type Classification = 'UNKNOWN' | 'EXPENSE' | 'INCOME' | 'REFUND' | 'TRANSFER' | 'EXCLUDED'
type Bucket = 'NEEDS' | 'WANTS' | 'SAVINGS'
type Subcategory = { id: number; name: string; budget_bucket: Bucket; is_active: boolean }
type Account = { id: number; name: string; is_active: boolean }
type Candidate = {
  id: number
  account: number
  account_name: string
  date: string
  posted_date: string | null
  raw_description: string
  merchant: string
  amount: string
  source_type: string
}
type Transaction = {
  id: number
  account: number
  account_name: string
  subcategory: number | null
  subcategory_name: string | null
  categorized_by_rule: number | null
  categorization_rule_conflict: boolean
  date: string
  posted_date: string | null
  raw_description: string
  merchant: string
  amount: string
  source_type: string
  classification: Classification
  budget_bucket: '' | Bucket
  review_status: 'PENDING' | 'REVIEWED' | 'NOT_REQUIRED'
  review_reasons: { code: string; label: string }[]
  duplicate_resolution: 'NONE' | 'UNRESOLVED' | 'DISTINCT' | 'CONFIRMED_DUPLICATE'
  duplicate_candidates: Candidate[]
  duplicate_of: number | null
  notes: string
}

type Props = { mode: 'transactions' | 'review'; reload: number }
type Draft = {
  classification: Classification
  subcategory: string
  merchant: string
  notes: string
  rememberRule: boolean
  ruleMatchValue: string
}
type SortField = 'date' | 'description' | 'account' | 'source_type' | 'classification' | 'subcategory' | 'review_status' | 'amount'

const classifications: Classification[] = [
  'UNKNOWN',
  'EXPENSE',
  'INCOME',
  'REFUND',
  'TRANSFER',
  'EXCLUDED',
]
const classificationLabels: Record<Classification, string> = {
  UNKNOWN: 'Unknown',
  EXPENSE: 'Expense',
  INCOME: 'Income',
  REFUND: 'Refund',
  TRANSFER: 'Transfer',
  EXCLUDED: 'Excluded',
}
const bucketLabels: Record<Bucket, string> = { NEEDS: 'Needs', WANTS: 'Wants', SAVINGS: 'Savings' }
const reviewStatusLabels: Record<Transaction['review_status'], string> = {
  PENDING: 'Pending review',
  REVIEWED: 'Reviewed',
  NOT_REQUIRED: 'No review needed',
}

function formatAmount(amount: string) {
  const negative = amount.startsWith('-')
  const [whole, fraction = '00'] = (negative ? amount.slice(1) : amount).split('.')
  return `${negative ? '-' : ''}$${whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',')}.${fraction.padEnd(2, '0')}`
}

function errorText(value: unknown): string {
  if (typeof value === 'string') return value
  if (value && typeof value === 'object') return JSON.stringify(value)
  return 'The request failed.'
}

function normalizedRuleValue(value: string) {
  return value.replace(/\s+/g, ' ').trim()
}

function acceptsBucket(classification: Classification, bucket: Bucket) {
  return (classification === 'EXPENSE' || classification === 'REFUND')
    ? bucket === 'NEEDS' || bucket === 'WANTS'
    : classification === 'TRANSFER' && bucket === 'SAVINGS'
}

function eligibleBuckets(classification: Classification): Bucket[] {
  if (classification === 'EXPENSE' || classification === 'REFUND') return ['NEEDS', 'WANTS']
  if (classification === 'TRANSFER') return ['SAVINGS']
  return []
}

export default function TransactionTable({ mode, reload }: Props) {
  const [transactions, setTransactions] = useState<Transaction[]>([])
  const [totalCount, setTotalCount] = useState(0)
  const [hasPreviousPage, setHasPreviousPage] = useState(false)
  const [hasNextPage, setHasNextPage] = useState(false)
  const [page, setPage] = useState(1)
  const [sort, setSort] = useState<{ field: SortField; direction: 'asc' | 'desc' }>({
    field: 'date',
    direction: 'desc',
  })
  const [accounts, setAccounts] = useState<Account[]>([])
  const [subcategories, setSubcategories] = useState<Subcategory[]>([])
  const [allSubcategories, setAllSubcategories] = useState<Subcategory[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [localReload, setLocalReload] = useState(0)
  const [accountFilter, setAccountFilter] = useState('')
  const [classificationFilter, setClassificationFilter] = useState('')
  const [sourceTypeFilter, setSourceTypeFilter] = useState('')
  const [subcategoryFilter, setSubcategoryFilter] = useState('')
  const [bucketFilter, setBucketFilter] = useState('')
  const [reviewStatusFilter, setReviewStatusFilter] = useState(
    mode === 'review' ? 'PENDING' : '',
  )
  const [reasonFilter, setReasonFilter] = useState('')
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [search, setSearch] = useState('')
  const [editingId, setEditingId] = useState<number | null>(null)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [selected, setSelected] = useState<number[]>([])
  const [bulkClassification, setBulkClassification] = useState<Classification | ''>('')
  const [bulkSubcategory, setBulkSubcategory] = useState('')
  const [pendingRulePrompt, setPendingRulePrompt] = useState<{
    id: number
    matching: number
    conflicting: number
  } | null>(null)

  useEffect(() => {
    setReviewStatusFilter(mode === 'review' ? 'PENDING' : '')
    setPage(1)
  }, [mode])

  useEffect(() => {
    let active = true
    Promise.all([
      apiFetch('/api/accounts/').then(async (response) => {
        if (!response.ok) throw new Error(`Account request failed (${response.status})`)
        return response.json() as Promise<Account[]>
      }),
      apiFetch('/api/subcategories/').then(async (response) => {
        if (!response.ok) throw new Error(`Subcategory request failed (${response.status})`)
        return response.json() as Promise<Subcategory[]>
      }),
    ]).then(([accountResult, subcategoryResult]) => {
      if (active) {
        setAccounts(accountResult)
        setSubcategories(subcategoryResult.filter((subcategory) => subcategory.is_active))
        setAllSubcategories(subcategoryResult)
      }
    }).catch((loadError: unknown) => {
      if (active) setError(loadError instanceof Error ? loadError.message : 'Unable to load edit options')
    })
    return () => { active = false }
  }, [])

  useEffect(() => {
    let active = true
    const params = new URLSearchParams()
    if (accountFilter) params.set('account', accountFilter)
    if (classificationFilter) params.set('classification', classificationFilter)
    if (sourceTypeFilter.trim()) params.set('source_type', sourceTypeFilter.trim())
    if (subcategoryFilter) params.set('subcategory', subcategoryFilter)
    if (bucketFilter) params.set('budget_bucket', bucketFilter)
    if (reviewStatusFilter) params.set('review_status', reviewStatusFilter)
    if (startDate) params.set('start_date', startDate)
    if (endDate) params.set('end_date', endDate)
    if (search.trim()) params.set('search', search.trim())
    if (mode === 'review' && reasonFilter) params.set('review_reason', reasonFilter)
    params.set('ordering', `${sort.direction === 'desc' ? '-' : ''}${sort.field}`)
    params.set('page', String(page))

    setLoading(true)
    setError('')
    apiFetch(`/api/transactions/?${params.toString()}`)
      .then(async (response) => {
        const result = await response.json()
        if (!response.ok) throw new Error(errorText(result.detail ?? result) || `Request failed (${response.status})`)
        if (active) {
          setTransactions(result.results as Transaction[])
          setTotalCount(result.count as number)
          setHasPreviousPage(Boolean(result.previous))
          setHasNextPage(Boolean(result.next))
        }
      })
      .catch((loadError: unknown) => {
        if (active) setError(loadError instanceof Error ? loadError.message : 'Unable to load transactions')
      })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [
    mode, reload, localReload, accountFilter, classificationFilter, sourceTypeFilter,
    subcategoryFilter, bucketFilter, reviewStatusFilter, reasonFilter, startDate,
    endDate, search, sort, page,
  ])

  useEffect(() => {
    setPage(1)
  }, [
    mode, accountFilter, classificationFilter, sourceTypeFilter, subcategoryFilter,
    bucketFilter, reviewStatusFilter, reasonFilter, startDate, endDate, search, sort,
  ])

  useEffect(() => {
    setSelected([])
    setEditingId(null)
    setDraft(null)
  }, [page])

  useEffect(() => {
    setSelected([])
    setEditingId(null)
    setDraft(null)
  }, [reload])

  function refresh() {
    setSelected([])
    setEditingId(null)
    setDraft(null)
    setLocalReload((current) => current + 1)
  }

  function beginEdit(item: Transaction) {
    setEditingId(item.id)
    setDraft({
      classification: item.classification,
      subcategory: item.subcategory ? String(item.subcategory) : '',
      merchant: item.merchant,
      notes: item.notes,
      rememberRule: false,
      ruleMatchValue: item.raw_description,
    })
  }

  async function saveEdit(event: FormEvent) {
    event.preventDefault()
    if (editingId === null || !draft) return
    setError('')
    try {
      const response = await apiFetch(`/api/transactions/${editingId}/${draft.rememberRule ? 'save-with-rule/' : ''}`, {
        method: draft.rememberRule ? 'POST' : 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          classification: draft.classification,
          subcategory: draft.subcategory || null,
          merchant: draft.merchant,
          notes: draft.notes,
          ...(draft.rememberRule ? { match_value: draft.ruleMatchValue } : {}),
        }),
      })
      const result = await response.json()
      if (!response.ok) throw new Error(errorText(result.detail ?? result) || `Save failed (${response.status})`)
      if (draft.rememberRule) {
        setNotice('Transaction updated and categorization rule created.')
        if (result.matching_pending_count > 0 || result.conflicting_pending_count > 0) {
          setPendingRulePrompt({
            id: result.rule.id as number,
            matching: result.matching_pending_count as number,
            conflicting: result.conflicting_pending_count as number,
          })
        }
      } else {
        setNotice('Transaction updated.')
        setPendingRulePrompt(null)
      }
      refresh()
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : 'Unable to save transaction')
    }
  }

  async function postAction(path: string, body?: object) {
    setError('')
    try {
      const response = await apiFetch(path, {
        method: 'POST',
        ...(body ? {
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        } : {}),
      })
      const result = await response.json()
      if (!response.ok) throw new Error(errorText(result.detail ?? result) || `Request failed (${response.status})`)
      setNotice('Transaction updated.')
      refresh()
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : 'The action failed')
    }
  }

  async function runBulk(operation: 'classification' | 'subcategory' | 'mark_reviewed') {
    const body: Record<string, unknown> = { operation, transaction_ids: selected }
    if (operation === 'classification') body.classification = bulkClassification
    if (operation === 'subcategory') body.subcategory = bulkSubcategory
    setError('')
    try {
      const response = await apiFetch('/api/transactions/bulk/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      const result = await response.json()
      if (!response.ok) throw new Error(errorText(result.detail ?? result) || `Bulk action failed (${response.status})`)
      setNotice(`Updated ${result.updated_count} transactions.`)
      refresh()
    } catch (bulkError) {
      setError(bulkError instanceof Error ? bulkError.message : 'Bulk action failed')
    }
  }

  async function applyPendingRule() {
    if (!pendingRulePrompt) return
    setError('')
    try {
      const response = await apiFetch(
        `/api/categorization-rules/${pendingRulePrompt.id}/apply-to-pending/`,
        { method: 'POST' },
      )
      const result = await response.json()
      if (!response.ok) throw new Error(errorText(result.detail ?? result))
      setNotice(
        `Applied the rule to ${result.updated_count} transactions.`
        + (result.conflicting_count ? ` ${result.conflicting_count} conflicting transactions remain in Review.` : ''),
      )
      setPendingRulePrompt(null)
      refresh()
    } catch (applyError) {
      setError(applyError instanceof Error ? applyError.message : 'Unable to apply rule')
    }
  }

  async function createSubcategory(
    id: number,
    name: string,
    budget_bucket: Bucket,
    classification: Classification,
  ) {
    const response = await apiFetch(`/api/transactions/${id}/create-subcategory-and-assign/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, budget_bucket, classification }),
    })
    const result = await response.json()
    if (!response.ok) throw new Error(errorText(result))
    const created = result.subcategory as Subcategory
    setSubcategories((current) => [...current, created])
    setTransactions((current) => current.map((item) => item.id === id ? result.transaction as Transaction : item))
    setDraft((current) => current ? {
      ...current,
      classification: result.transaction.classification,
      subcategory: String(created.id),
    } : current)
    setNotice('Subcategory created and assigned.')
    return created
  }

  const allVisibleSelected = transactions.length > 0 && transactions.every((item) => selected.includes(item.id))
  const columnCount = mode === 'review' ? 10 : 9

  function sortBy(field: SortField) {
    setSort((current) => current.field === field
      ? { field, direction: current.direction === 'asc' ? 'desc' : 'asc' }
      : { field, direction: field === 'date' ? 'desc' : 'asc' })
  }

  return (
    <section className="ledger-card" aria-labelledby="transaction-table-heading">
      <div className="ledger-heading">
        <div>
          <h2 id="transaction-table-heading">{mode === 'review' ? 'Pending review' : 'All transactions'}</h2>
          <p>{loading ? 'Loading transactions…' : `${totalCount} transactions`}</p>
        </div>
      </div>

      <div className="ledger-filters">
        <label>
          Account
          <select value={accountFilter} onChange={(event) => setAccountFilter(event.target.value)}>
            <option value="">All accounts</option>
            {accounts.map((account) => <option key={account.id} value={account.id}>{account.name}</option>)}
          </select>
        </label>
        <label>
          Classification
          <select value={classificationFilter} onChange={(event) => setClassificationFilter(event.target.value)}>
            <option value="">All classifications</option>
            {classifications.map((classification) => (
              <option key={classification} value={classification}>{classificationLabels[classification]}</option>
            ))}
          </select>
        </label>
        <label>
          Source type
          <input value={sourceTypeFilter} onChange={(event) => setSourceTypeFilter(event.target.value)} />
        </label>
        <label>
          Subcategory
          <select value={subcategoryFilter} onChange={(event) => setSubcategoryFilter(event.target.value)}>
            <option value="">All subcategories</option>
            <option value="uncategorized">Uncategorized</option>
            {allSubcategories.map((subcategory) => (
              <option key={subcategory.id} value={subcategory.id}>
                {subcategory.name}{subcategory.is_active ? '' : ' (archived)'}
              </option>
            ))}
          </select>
        </label>
        <label>
          Bucket
          <select value={bucketFilter} onChange={(event) => setBucketFilter(event.target.value)}>
            <option value="">All buckets</option>
            {(['NEEDS', 'WANTS', 'SAVINGS'] as Bucket[]).map((value) => (
              <option key={value} value={value}>{bucketLabels[value]}</option>
            ))}
          </select>
        </label>
        <label>
          Review status
          <select value={reviewStatusFilter} onChange={(event) => setReviewStatusFilter(event.target.value)}>
            <option value="">All statuses</option>
            <option value="PENDING">Pending review</option>
            <option value="REVIEWED">Reviewed</option>
            <option value="NOT_REQUIRED">No review needed</option>
          </select>
        </label>
        {mode === 'review' && (
          <label>
            Review reason
            <select value={reasonFilter} onChange={(event) => setReasonFilter(event.target.value)}>
              <option value="">All reasons</option>
              <option value="UNKNOWN">Unknown classification</option>
              <option value="UNCATEGORIZED">Uncategorized expense/refund</option>
              <option value="POSSIBLE_DUPLICATE">Possible duplicate</option>
              <option value="TRANSFER">Transfer</option>
              <option value="RULE_CONFLICT">Rule conflict</option>
            </select>
          </label>
        )}
        <label>
          From
          <input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} />
        </label>
        <label>
          To
          <input type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} />
        </label>
        <label className="search-filter">
          Search
          <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Description, merchant, notes" />
        </label>
      </div>

      {mode === 'review' && selected.length > 0 && (
        <div className="bulk-toolbar">
          <strong>{selected.length} selected</strong>
          <label>
            Classification
            <select value={bulkClassification} onChange={(event) => setBulkClassification(event.target.value as Classification | '')}>
              <option value="">Choose…</option>
              {classifications.map((classification) => (
                <option key={classification} value={classification}>{classificationLabels[classification]}</option>
              ))}
            </select>
          </label>
          <button type="button" onClick={() => void runBulk('classification')} disabled={!bulkClassification}>
            Set classification
          </button>
          <label>
            Subcategory
            <select value={bulkSubcategory} onChange={(event) => setBulkSubcategory(event.target.value)}>
              <option value="">Choose…</option>
              {subcategories.map((subcategory) => (
                <option key={subcategory.id} value={subcategory.id}>
                  {subcategory.name} · {bucketLabels[subcategory.budget_bucket]}
                </option>
              ))}
            </select>
          </label>
          <button type="button" onClick={() => void runBulk('subcategory')} disabled={!bulkSubcategory}>
            Assign subcategory
          </button>
          <button type="button" onClick={() => void runBulk('mark_reviewed')}>Mark reviewed</button>
        </div>
      )}

      {notice && <p className="table-notice" role="status">{notice}</p>}
      {error && <p className="message message--error" role="alert">{error}</p>}
      {pendingRulePrompt && mode === 'review' && (
        <div className="rule-apply-prompt" role="status">
          <span>
            {pendingRulePrompt.matching > 0
              ? `${pendingRulePrompt.matching} other pending transaction${pendingRulePrompt.matching === 1 ? '' : 's'} match this rule. Apply it?`
              : 'No compatible pending transactions can be applied.'}
            {pendingRulePrompt.conflicting > 0
              ? ` ${pendingRulePrompt.conflicting} conflicting match${pendingRulePrompt.conflicting === 1 ? '' : 'es'} will remain in Review.`
              : ''}
          </span>
          {pendingRulePrompt.matching > 0 && (
            <button type="button" className="primary-button" onClick={() => void applyPendingRule()}>
              Apply to matching pending transactions
            </button>
          )}
          <button type="button" className="quiet-button" onClick={() => setPendingRulePrompt(null)}>Not now</button>
        </div>
      )}
      {loading ? (
        <p className="message" role="status">Loading transactions…</p>
      ) : transactions.length === 0 ? (
        <div className="empty-state">
          <h3>{mode === 'review' ? 'Review queue is clear' : 'No transactions found'}</h3>
          <p>{mode === 'review' ? 'Transactions needing attention will appear here.' : 'Try changing the filters or import a CSV.'}</p>
        </div>
      ) : (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                {mode === 'review' && (
                  <th scope="col">
                    <input
                      type="checkbox"
                      aria-label="Select all visible transactions"
                      checked={allVisibleSelected}
                      onChange={() => setSelected(allVisibleSelected ? [] : transactions.map((item) => item.id))}
                    />
                  </th>
                )}
                <SortableHeader label="Date" field="date" sort={sort} onSort={sortBy} />
                <SortableHeader label="Description" field="description" sort={sort} onSort={sortBy} />
                <SortableHeader label="Account" field="account" sort={sort} onSort={sortBy} />
                <SortableHeader label="Source type" field="source_type" sort={sort} onSort={sortBy} />
                <SortableHeader label="Classification" field="classification" sort={sort} onSort={sortBy} />
                <SortableHeader label="Subcategory" field="subcategory" sort={sort} onSort={sortBy} />
                <SortableHeader label="Review" field="review_status" sort={sort} onSort={sortBy} />
                <SortableHeader label="Amount" field="amount" sort={sort} onSort={sortBy} className="amount-cell" />
                <th scope="col">Actions</th>
              </tr>
            </thead>
            <tbody>
              {transactions.map((item) => (
                <FragmentRow
                  key={item.id}
                  item={item}
                  mode={mode}
                  columnCount={columnCount}
                  selected={selected.includes(item.id)}
                  editing={editingId === item.id}
                  draft={editingId === item.id ? draft : null}
                  subcategories={subcategories}
                  onSelect={() => setSelected((current) => current.includes(item.id)
                    ? current.filter((id) => id !== item.id)
                    : [...current, item.id])}
                  onEdit={() => beginEdit(item)}
                  onCancel={() => { setEditingId(null); setDraft(null) }}
                  onDraft={(value) => setDraft(value)}
                  onSave={saveEdit}
                  onCreateSubcategory={createSubcategory}
                  reviewStatusLabel={reviewStatusLabels[item.review_status]}
                  onMarkReviewed={() => void postAction(`/api/transactions/${item.id}/mark-reviewed/`)}
                  onResolve={(resolution, candidateId) => void postAction(
                    `/api/transactions/${item.id}/resolve-duplicate/`,
                    { resolution, ...(candidateId ? { duplicate_of: candidateId } : {}) },
                  )}
                />
              ))}
            </tbody>
          </table>
          <div className="ledger-pagination" aria-label="Transaction pages">
            <span>
              {totalCount === 0 ? '0 transactions' : `${(page - 1) * 100 + 1}–${Math.min(page * 100, totalCount)} of ${totalCount}`}
            </span>
            <div>
              <button type="button" disabled={!hasPreviousPage || loading} onClick={() => setPage((current) => current - 1)}>
                Previous
              </button>
              <span>Page {page} of {Math.max(1, Math.ceil(totalCount / 100))}</span>
              <button type="button" disabled={!hasNextPage || loading} onClick={() => setPage((current) => current + 1)}>
                Next
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  )
}

function SortableHeader({
  label,
  field,
  sort,
  onSort,
  className = '',
}: {
  label: string
  field: SortField
  sort: { field: SortField; direction: 'asc' | 'desc' }
  onSort: (field: SortField) => void
  className?: string
}) {
  const active = sort.field === field
  const direction = active ? sort.direction : null
  return (
    <th scope="col" aria-sort={direction === 'asc' ? 'ascending' : direction === 'desc' ? 'descending' : 'none'} className={className}>
      <button
        className="sort-header"
        type="button"
        aria-label={`Sort by ${label}${active ? `, currently ${direction}ending` : ''}`}
        onClick={() => onSort(field)}
      >
        {label}<span aria-hidden="true">{direction === 'asc' ? ' ↑' : direction === 'desc' ? ' ↓' : ' ↕'}</span>
      </button>
    </th>
  )
}

type RowProps = {
  item: Transaction
  mode: Props['mode']
  columnCount: number
  selected: boolean
  editing: boolean
  draft: Draft | null
  subcategories: Subcategory[]
  onSelect: () => void
  onEdit: () => void
  onCancel: () => void
  onDraft: (draft: Draft) => void
  onSave: (event: FormEvent) => void
  onCreateSubcategory: (
    id: number,
    name: string,
    bucket: Bucket,
    classification: Classification,
  ) => Promise<Subcategory>
  reviewStatusLabel: string
  onMarkReviewed: () => void
  onResolve: (resolution: 'DISTINCT' | 'CONFIRMED_DUPLICATE', candidateId?: number) => void
}

function FragmentRow(props: RowProps) {
  const { item, mode, columnCount, selected, editing, draft, subcategories, reviewStatusLabel } = props
  const [candidateId, setCandidateId] = useState<number | ''>(item.duplicate_candidates[0]?.id ?? '')
  const [createName, setCreateName] = useState('')
  const [createBucket, setCreateBucket] = useState<Bucket>('NEEDS')
  const [createError, setCreateError] = useState('')
  const [creating, setCreating] = useState(false)
  const eligibleSubcategories = draft
    ? subcategories.filter((subcategory) => acceptsBucket(draft.classification, subcategory.budget_bucket))
    : []
  const assignableBuckets = draft ? eligibleBuckets(draft.classification) : []

  return (
    <>
      <tr>
        {mode === 'review' && (
          <td>
            <input type="checkbox" aria-label={`Select transaction ${item.id}`} checked={selected} onChange={props.onSelect} />
          </td>
        )}
        <td className="date-cell">{item.date}</td>
        <td>
          <div className="description">
            <span>{item.merchant || item.raw_description}</span>
            {item.merchant && <small>{item.raw_description}</small>}
          </div>
        </td>
        <td>{item.account_name}</td>
        <td>{item.source_type || '—'}</td>
        <td><span className={`classification classification--${item.classification.toLowerCase()}`}>{classificationLabels[item.classification]}</span></td>
        <td>
          {item.subcategory_name ? (
            <span>{item.subcategory_name}<small className="bucket-label">{item.budget_bucket && bucketLabels[item.budget_bucket]}</small></span>
          ) : <span className="muted">Uncategorized</span>}
          {item.categorized_by_rule && (
            <small className="rule-provenance">Automatically categorized · Rule #{item.categorized_by_rule}</small>
          )}
        </td>
        <td>
          {mode === 'review' && (
            <small className="review-reasons">{item.review_reasons.map((reason) => reason.label).join(' · ') || item.review_status}</small>
          )}
          {mode === 'transactions' && (
            <small className="review-reasons">{reviewStatusLabel}</small>
          )}
          {item.duplicate_resolution === 'DISTINCT' && (
            <small className="review-reasons">Possible duplicate reviewed as distinct</small>
          )}
          {item.duplicate_resolution === 'CONFIRMED_DUPLICATE' && (
            <small className="review-reasons">Confirmed duplicate of #{item.duplicate_of}</small>
          )}
        </td>
        <td className={`amount-cell ${item.amount.startsWith('-') ? 'amount-negative' : 'amount-positive'}`}>{formatAmount(item.amount)}</td>
        <td className="row-actions">
          <button type="button" onClick={props.onEdit}>Edit</button>
          {mode === 'review' && (
            <button type="button" onClick={props.onMarkReviewed}>Mark reviewed</button>
          )}
        </td>
      </tr>
      {editing && draft && (
        <tr className="detail-row">
          <td colSpan={columnCount}>
            <form className="transaction-editor" onSubmit={props.onSave}>
              <label>
                Classification
                <select
                  value={draft.classification}
                  onChange={(event) => {
                    const classification = event.target.value as Classification
                    const nextSubcategory = subcategories.find((category) => category.id === Number(draft.subcategory))
                    props.onDraft({
                      ...draft,
                      classification,
                      subcategory: nextSubcategory && acceptsBucket(classification, nextSubcategory.budget_bucket)
                        ? draft.subcategory
                        : '',
                      rememberRule: false,
                    })
                  }}
                >
                  {classifications.map((classification) => (
                    <option key={classification} value={classification}>{classificationLabels[classification]}</option>
                  ))}
                </select>
              </label>
              <label>
                Subcategory
                <select
                  value={draft.subcategory}
                  onChange={(event) => props.onDraft({
                    ...draft,
                    subcategory: event.target.value,
                    rememberRule: event.target.value ? draft.rememberRule : false,
                  })}
                >
                  <option value="">No subcategory</option>
                  {eligibleSubcategories.map((subcategory) => (
                    <option key={subcategory.id} value={subcategory.id}>
                      {subcategory.name} · {bucketLabels[subcategory.budget_bucket]}
                    </option>
                  ))}
                </select>
              </label>
              {mode === 'review' && assignableBuckets.length > 0 && (
                <details className="inline-category-create">
                  <summary>+ Create new subcategory</summary>
                  <div className="inline-category-fields">
                    <label>
                      Name
                      <input
                        value={createName}
                        maxLength={100}
                        onKeyDown={(event) => {
                          if (event.key === 'Enter') event.preventDefault()
                        }}
                        onChange={(event) => setCreateName(event.target.value)}
                      />
                    </label>
                    <label>
                      Bucket
                      <select
                        value={assignableBuckets.includes(createBucket) ? createBucket : assignableBuckets[0]}
                        onChange={(event) => setCreateBucket(event.target.value as Bucket)}
                      >
                        {assignableBuckets.map((value) => (
                          <option key={value} value={value}>{bucketLabels[value]}</option>
                        ))}
                      </select>
                    </label>
                    <button
                      className="secondary-button"
                      type="button"
                      disabled={!createName.trim() || creating}
                      onClick={async () => {
                        setCreating(true)
                        setCreateError('')
                        try {
                          const validBucket = assignableBuckets.includes(createBucket)
                            ? createBucket
                            : assignableBuckets[0]
                          await props.onCreateSubcategory(
                            item.id,
                            createName,
                            validBucket,
                            draft.classification,
                          )
                          setCreateName('')
                          setCreateError('')
                        } catch (createFailure) {
                          setCreateError(createFailure instanceof Error ? createFailure.message : 'Unable to create subcategory')
                        } finally {
                          setCreating(false)
                        }
                      }}
                    >
                      {creating ? 'Creating…' : 'Create and assign'}
                    </button>
                    {createError && <p className="inline-create-error" role="alert">{createError}</p>}
                  </div>
                </details>
              )}
              {mode === 'review'
                && draft.classification === 'EXPENSE'
                && Boolean(draft.subcategory)
                && (
                  <div className="remember-rule">
                    <label className="remember-rule-toggle">
                      <input
                        type="checkbox"
                        checked={draft.rememberRule}
                        onChange={(event) => props.onDraft({
                          ...draft,
                          rememberRule: event.target.checked,
                          ruleMatchValue: draft.ruleMatchValue || item.raw_description,
                        })}
                      />
                      Remember this for future matching expenses
                    </label>
                    {draft.rememberRule && (
                      <>
                        <label>
                          Description fragment (whitespace collapsed when saved)
                          <input
                            value={draft.ruleMatchValue}
                            maxLength={200}
                            required
                            onChange={(event) => props.onDraft({ ...draft, ruleMatchValue: event.target.value })}
                          />
                        </label>
                        <p>
                          When an EXPENSE description contains “{normalizedRuleValue(draft.ruleMatchValue) || '…'}”,
                          assign {subcategories.find((category) => category.id === Number(draft.subcategory))?.name}.
                        </p>
                      </>
                    )}
                  </div>
                )}
              <label>
                Clean merchant
                <input value={draft.merchant} maxLength={200} onChange={(event) => props.onDraft({ ...draft, merchant: event.target.value })} />
              </label>
              <label className="notes-editor">
                Notes
                <textarea value={draft.notes} onChange={(event) => props.onDraft({ ...draft, notes: event.target.value })} rows={2} />
              </label>
              <div className="editor-actions">
                <button className="primary-button" type="submit">Save changes</button>
                <button className="refresh-button" type="button" onClick={props.onCancel}>Cancel</button>
              </div>
            </form>
            {item.duplicate_resolution === 'UNRESOLVED' && (
              <div className="duplicate-context">
                <h3>Possible duplicate comparison</h3>
                <p>These existing transactions share the account, date, and amount, plus the description or source type.</p>
                {item.duplicate_candidates.map((candidate) => (
                  <div className="candidate-row" key={candidate.id}>
                    <span><strong>Candidate #{candidate.id}</strong> · {candidate.account_name} · {candidate.date}</span>
                    <span>{candidate.merchant || candidate.raw_description}</span>
                    <span>Raw: {candidate.raw_description}</span>
                    <span>{candidate.source_type || '—'} · {formatAmount(candidate.amount)}</span>
                  </div>
                ))}
                <div className="duplicate-actions">
                  {item.duplicate_candidates.length > 1 && (
                    <label>
                      Duplicate of
                      <select value={candidateId} onChange={(event) => setCandidateId(Number(event.target.value))}>
                        {item.duplicate_candidates.map((candidate) => (
                          <option key={candidate.id} value={candidate.id}>
                            #{candidate.id} · {candidate.date} · {candidate.merchant || candidate.raw_description}
                          </option>
                        ))}
                      </select>
                    </label>
                  )}
                  <button type="button" onClick={() => props.onResolve('DISTINCT')}>Mark distinct</button>
                  <button
                    type="button"
                    disabled={item.duplicate_candidates.length === 0}
                    onClick={() => props.onResolve('CONFIRMED_DUPLICATE', Number(candidateId))}
                  >
                    Confirm duplicate
                  </button>
                </div>
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  )
}

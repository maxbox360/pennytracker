import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { apiFetch } from './api'

type Classification = 'UNKNOWN' | 'EXPENSE' | 'INCOME' | 'REFUND' | 'TRANSFER' | 'EXCLUDED' | 'OTHER_INFLOW'
type Bucket = 'NEEDS' | 'WANTS' | 'SAVINGS'
type Scope = 'BUDGET' | 'OTHER_INFLOW'
type SavingsEffect = 'NONE' | 'CONTRIBUTION' | 'WITHDRAWAL'
type Subcategory = { id: number; name: string; scope: Scope; budget_bucket: Bucket | null; is_active: boolean }
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
  savings_effect: SavingsEffect
  budget_bucket: '' | Bucket
  review_status: 'PENDING' | 'REVIEWED' | 'NOT_REQUIRED'
  review_reasons: { code: string; label: string }[]
  duplicate_resolution: 'NONE' | 'UNRESOLVED' | 'DISTINCT' | 'CONFIRMED_DUPLICATE'
  duplicate_candidates: Candidate[]
  duplicate_of: number | null
  notes: string
}

type Props = { mode: 'transactions' | 'review'; reload: number; initialReason?: string; initialStartDate?: string; initialEndDate?: string }
type Draft = {
  classification: Classification
  savings_effect: SavingsEffect
  subcategory: string
  merchant: string
  notes: string
  rememberRule: boolean
  ruleMatchValue: string
  ruleMatchType: 'CONTAINS' | 'STARTS_WITH'
  resolveConflict: boolean
  confirmOutcome: boolean
}
type SortField = 'date' | 'description' | 'account' | 'source_type' | 'classification' | 'subcategory' | 'review_status' | 'amount'

const classifications: Classification[] = [
  'UNKNOWN',
  'EXPENSE',
  'INCOME',
  'REFUND',
  'TRANSFER',
  'EXCLUDED',
  'OTHER_INFLOW',
]
const classificationLabels: Record<Classification, string> = {
  UNKNOWN: 'Unknown',
  EXPENSE: 'Expense',
  INCOME: 'Income',
  REFUND: 'Refund',
  TRANSFER: 'Transfer',
  EXCLUDED: 'Excluded',
  OTHER_INFLOW: 'Other inflow',
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

function acceptsSubcategory(classification: Classification, subcategory: Subcategory) {
  return classification === 'OTHER_INFLOW'
    ? subcategory.scope === 'OTHER_INFLOW' && subcategory.budget_bucket === null
    : subcategory.scope === 'BUDGET'
      && subcategory.budget_bucket !== null
      && acceptsBucket(classification, subcategory.budget_bucket)
}

function financialIntent(draft: Draft, original: Transaction) {
  return draft.confirmOutcome || draft.resolveConflict
    || draft.classification !== original.classification
    || draft.savings_effect !== original.savings_effect
    || (draft.subcategory ? Number(draft.subcategory) : null) !== original.subcategory
}

function canRememberOutcome(draft: Draft, subcategory: Subcategory | undefined) {
  if (draft.classification === 'UNKNOWN') return false
  if (draft.subcategory && (!subcategory?.is_active || !acceptsSubcategory(draft.classification, subcategory))) return false
  if (draft.classification === 'EXPENSE' || draft.classification === 'REFUND') return Boolean(subcategory)
  if (draft.classification === 'TRANSFER') {
    return draft.savings_effect === 'NONE' ? !draft.subcategory : Boolean(subcategory)
  }
  return true
}

function ruleIneligibility(draft: Draft, subcategory: Subcategory | undefined, original: Transaction) {
  if (!normalizedRuleValue(draft.ruleMatchValue)) return 'Enter a match value.'
  if (!financialIntent(draft, original)) {
    return 'Not eligible: change the financial outcome or confirm it before remembering.'
  }
  const needsSubcategory = draft.classification === 'EXPENSE' || draft.classification === 'REFUND'
    || (draft.classification === 'TRANSFER' && draft.savings_effect !== 'NONE')
  if (needsSubcategory && !subcategory) return 'Not eligible: this outcome requires a subcategory.'
  if (draft.classification === 'TRANSFER' && draft.savings_effect !== 'NONE' && subcategory?.budget_bucket !== 'SAVINGS') {
    return 'Not eligible: a Savings effect requires a Savings subcategory.'
  }
  if (draft.classification === 'TRANSFER' && draft.savings_effect === 'NONE' && subcategory) {
    return 'Not eligible: an ordinary Transfer rule cannot assign a subcategory.'
  }
  if (subcategory && !acceptsSubcategory(draft.classification, subcategory)) {
    return 'Not eligible: the subcategory does not fit this classification.'
  }
  return null
}

export default function TransactionTable({ mode, reload, initialReason = '', initialStartDate = '', initialEndDate = '' }: Props) {
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
  const [reasonFilter, setReasonFilter] = useState(initialReason)
  const [startDate, setStartDate] = useState(initialStartDate)
  const [endDate, setEndDate] = useState(initialEndDate)
  const [search, setSearch] = useState('')
  const [editingId, setEditingId] = useState<number | null>(null)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [selected, setSelected] = useState<number[]>([])
  const [bulkClassification, setBulkClassification] = useState<Classification | ''>('')
  const [bulkSubcategory, setBulkSubcategory] = useState('')
  const [bulkMerchant, setBulkMerchant] = useState('')
  const [bulkSavingsEffect, setBulkSavingsEffect] = useState<SavingsEffect>('CONTRIBUTION')
  const [bulkSavingsSubcategory, setBulkSavingsSubcategory] = useState('')
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
  }, [
    page, mode, accountFilter, classificationFilter, sourceTypeFilter, subcategoryFilter,
    bucketFilter, reviewStatusFilter, reasonFilter, startDate, endDate, search, sort,
  ])

  useEffect(() => {
    setSelected([])
    setEditingId(null)
    setDraft(null)
  }, [reload])

  function refresh() {
    setSelected([])
    setEditingId(null)
    setDraft(null)
    setPage(1)
    setLocalReload((current) => current + 1)
  }

  function beginEdit(item: Transaction) {
    setEditingId(item.id)
    setDraft({
      classification: item.classification,
      savings_effect: item.savings_effect,
      subcategory: item.subcategory ? String(item.subcategory) : '',
      merchant: item.merchant,
      notes: item.notes,
      rememberRule: false,
      ruleMatchValue: item.raw_description,
      ruleMatchType: 'CONTAINS',
      resolveConflict: false,
      confirmOutcome: false,
    })
  }

  async function saveEdit(event: FormEvent) {
    event.preventDefault()
    if (editingId === null || !draft) return
    const original = transactions.find((item) => item.id === editingId)
    if (!original) return
    setError('')
    const reviewing = mode === 'review' && original.review_status === 'PENDING'
    const subcategory = draft.subcategory ? Number(draft.subcategory) : null
    const changed: Record<string, unknown> = {}
    if (draft.classification !== original.classification) changed.classification = draft.classification
    if (draft.savings_effect !== original.savings_effect) changed.savings_effect = draft.savings_effect
    if (subcategory !== original.subcategory) changed.subcategory = subcategory
    if (draft.merchant !== original.merchant) changed.merchant = draft.merchant
    if (draft.notes !== original.notes) changed.notes = draft.notes
    const remember = reviewing && draft.rememberRule
    const resolve = {
      ...(reviewing && draft.resolveConflict ? { resolve_rule_conflict: true } : {}),
      ...(reviewing && draft.confirmOutcome && draft.classification !== 'UNKNOWN' && !(subcategory === null && (draft.classification === 'EXPENSE' || draft.classification === 'REFUND' || (draft.classification === 'TRANSFER' && draft.savings_effect !== 'NONE'))) ? { confirm_financial_outcome: true } : {}),
    }
    const path = !reviewing
      ? `/api/transactions/${editingId}/`
      : `/api/transactions/${editingId}/${remember ? 'save-with-rule/' : 'review-save/'}`
    try {
      const response = await apiFetch(path, {
        method: reviewing ? 'POST' : 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(!reviewing ? changed : {
          ...changed,
          ...resolve,
          ...(remember ? {
            match_type: draft.ruleMatchType,
            match_value: normalizedRuleValue(draft.ruleMatchValue),
          } : {}),
        }),
      })
      const result = await response.json()
      if (!response.ok) throw new Error(errorText(result.detail ?? result) || `Save failed (${response.status})`)
      const saved = (remember ? result.transaction : result) as Transaction
      if (reviewing) {
        setTransactions((current) => saved.review_status === 'PENDING'
          ? current.map((item) => item.id === saved.id ? saved : item)
          : current.filter((item) => item.id !== saved.id))
      }
      const blockers = reviewing && saved.review_status === 'PENDING'
        ? ` Still pending review: ${saved.review_reasons.map((reason) => reason.label).join(' · ') || 'see transaction'}.`
        : ''
      if (remember) {
        setNotice(`Transaction updated and rule #${result.rule.id} created.${blockers}`)
        setPendingRulePrompt(result.matching_pending_count > 0 || result.conflicting_pending_count > 0
          ? {
            id: result.rule.id as number,
            matching: result.matching_pending_count as number,
            conflicting: result.conflicting_pending_count as number,
          }
          : null)
      } else {
        setNotice(`Transaction updated.${blockers}`)
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

  async function runBulk(operation: 'classification' | 'subcategory' | 'mark_reviewed' | 'merchant' | 'savings_effect') {
    const body: Record<string, unknown> = { operation, transaction_ids: selected }
    if (operation === 'classification') body.classification = bulkClassification
    if (operation === 'subcategory') body.subcategory = bulkSubcategory
    if (operation === 'merchant') body.merchant = bulkMerchant
    if (operation === 'savings_effect') {
      body.savings_effect = bulkSavingsEffect
      body.subcategory = bulkSavingsEffect === 'NONE' ? null : bulkSavingsSubcategory
    }
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
    budget_bucket: Bucket | null,
    classification: Classification,
    savings_effect: SavingsEffect,
  ) {
    const response = await apiFetch(`/api/transactions/${id}/create-subcategory-and-assign/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name,
        scope: classification === 'OTHER_INFLOW' ? 'OTHER_INFLOW' : 'BUDGET',
        budget_bucket,
        classification,
        savings_effect,
      }),
    })
    const result = await response.json()
    if (!response.ok) throw new Error(errorText(result))
    const created = result.subcategory as Subcategory
    setSubcategories((current) => [...current, created])
    setAllSubcategories((current) => [...current, created])
    setTransactions((current) => current.map((item) => item.id === id ? result.transaction as Transaction : item))
    setDraft((current) => current ? {
      ...current,
      classification: result.transaction.classification,
      savings_effect: result.transaction.savings_effect,
      subcategory: String(created.id),
      resolveConflict: result.transaction.categorization_rule_conflict ? current.resolveConflict : false,
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

  function exportQueryString() {
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
    return params.toString()
  }

  const savingsSubcategories = subcategories.filter(
    (subcategory) => subcategory.scope === 'BUDGET' && subcategory.budget_bucket === 'SAVINGS',
  )

  const totalPages = Math.max(1, Math.ceil(totalCount / 100))
  let pageInflowCents = 0
  let pageOutflowCents = 0
  let pageCategorizedCount = 0
  let pagePendingReviewCount = 0
  let pageDuplicateCount = 0
  for (const item of transactions) {
    const cents = Math.round(Number(item.amount) * 100)
    if (!Number.isNaN(cents)) {
      if (cents < 0) pageOutflowCents += Math.abs(cents)
      else pageInflowCents += cents
    }
    if (item.subcategory !== null) pageCategorizedCount += 1
    if (item.review_status === 'PENDING') pagePendingReviewCount += 1
    if (item.duplicate_resolution === 'UNRESOLVED') pageDuplicateCount += 1
  }
  const pageUncategorizedCount = transactions.length - pageCategorizedCount
  function formatCents(cents: number, negative = false) {
    const abs = Math.abs(cents)
    const whole = Math.floor(abs / 100)
    const fraction = String(abs % 100).padStart(2, '0')
    return formatAmount(`${negative && abs > 0 ? '-' : ''}${whole}.${fraction}`)
  }

  return (
    <section className="ledger-card" aria-labelledby="transaction-table-heading">
      <div className="ledger-heading" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '12px' }}>
        <div>
          <h2 id="transaction-table-heading">{mode === 'review' ? 'Pending review' : 'All transactions'}</h2>
          <p>{loading ? 'Loading transactions…' : `${totalCount} transactions`}</p>
        </div>
        <a
          className="secondary-button"
          href={`/api/transactions/export-csv/?${exportQueryString()}`}
          download
          style={{ textDecoration: 'none', display: 'inline-flex', alignItems: 'center' }}
        >
          Export filtered CSV
        </a>
      </div>

      {!loading && transactions.length > 0 && (
        <div className="kpi-strip">
          <div className="kpi-card">
            <div className="kpi-card-header">
              <span className="kpi-label">Total Matching</span>
              <span className="kpi-icon-badge" aria-hidden="true">
                <svg width="18" height="18" viewBox="0 0 20 20" fill="none">
                  <rect x="2.75" y="3.5" width="14.5" height="13" rx="3" fill="var(--info-soft)" stroke="var(--chart-needs)" strokeWidth="1.5" />
                  <path d="M6 7.5h8M6 10.5h8M6 13.5h5" stroke="var(--chart-needs)" strokeWidth="1.5" strokeLinecap="round" />
                </svg>
              </span>
            </div>
            <strong className="kpi-value">{totalCount.toLocaleString()}</strong>
            <span className="kpi-sub">
              Page {page} of {totalPages} · {transactions.length} visible
            </span>
          </div>
          <div className="kpi-card kpi-card--accent">
            <div className="kpi-card-header">
              <span className="kpi-label">Page Inflow / Outflow</span>
              <span className="kpi-icon-badge" aria-hidden="true">
                <svg width="18" height="18" viewBox="0 0 20 20" fill="none">
                  <circle cx="10" cy="10" r="7.25" fill="var(--accent-soft)" stroke="var(--accent)" strokeWidth="1.5" />
                  <path d="M7.5 13V7m0 0L5.25 9.25M7.5 7l2.25 2.25" stroke="var(--positive)" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
                  <path d="M12.5 7v6m0 0 2.25-2.25M12.5 13l-2.25-2.25" stroke="var(--negative)" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </span>
            </div>
            <strong className="kpi-value">
              {formatCents(pageInflowCents)} / {formatCents(pageOutflowCents, true)}
            </strong>
            <span className="kpi-sub">
              Inflow {formatCents(pageInflowCents)} · Outflow {formatCents(pageOutflowCents, true)}
            </span>
            <div className="progress-track kpi-spark-bar" aria-hidden="true" style={{ display: 'flex' }}>
              <div
                className="progress-fill progress-fill--positive"
                style={{
                  width: `${pageInflowCents + pageOutflowCents > 0
                    ? Math.round((pageInflowCents / (pageInflowCents + pageOutflowCents)) * 100)
                    : 50}%`,
                }}
              />
              <div
                className="progress-fill progress-fill--negative"
                style={{
                  width: `${pageInflowCents + pageOutflowCents > 0
                    ? 100 - Math.round((pageInflowCents / (pageInflowCents + pageOutflowCents)) * 100)
                    : 50}%`,
                }}
              />
            </div>
          </div>
          {mode === 'review' ? (
            <div className="kpi-card kpi-card--warning">
              <div className="kpi-card-header">
                <span className="kpi-label">Review Queue</span>
                <span className="kpi-icon-badge" aria-hidden="true">
                  <svg width="18" height="18" viewBox="0 0 20 20" fill="none">
                    <path d="M10 2.75l6.5 2.5v4.5c0 4.3-2.8 7.2-6.5 8.5-3.7-1.3-6.5-4.2-6.5-8.5v-4.5l6.5-2.5z" fill="var(--warning-soft)" stroke="var(--warning)" strokeWidth="1.5" />
                    <path d="M10 7v3.5M10 13h.01" stroke="var(--warning)" strokeWidth="1.75" strokeLinecap="round" />
                  </svg>
                </span>
              </div>
              <strong className="kpi-value">
                {selected.length > 0 ? `${selected.length} checked` : `${pagePendingReviewCount} pending`}
              </strong>
              <span className="kpi-sub">
                {selected.length} selected · {pageDuplicateCount} possible duplicate{pageDuplicateCount === 1 ? '' : 's'}
              </span>
            </div>
          ) : (
            <div className="kpi-card">
              <div className="kpi-card-header">
                <span className="kpi-label">Page Categorization</span>
                <span className="kpi-icon-badge" aria-hidden="true">
                  <svg width="18" height="18" viewBox="0 0 20 20" fill="none">
                    <circle cx="10" cy="10" r="7.25" fill="var(--positive-soft)" stroke="var(--positive)" strokeWidth="1.5" />
                    <path d="M6.75 10.25l2.25 2.25 4.5-4.75" stroke="var(--positive)" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </span>
              </div>
              <strong className="kpi-value">
                {pageCategorizedCount} / {transactions.length}
              </strong>
              <span className="kpi-sub">
                {pageCategorizedCount} categorized · {pageUncategorizedCount} uncategorized
              </span>
            </div>
          )}
        </div>
      )}

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
                  {subcategory.name} · {subcategory.budget_bucket
                    ? bucketLabels[subcategory.budget_bucket]
                    : 'Other Inflows'}
                </option>
              ))}
            </select>
          </label>
          <button type="button" onClick={() => void runBulk('subcategory')} disabled={!bulkSubcategory}>
            Assign subcategory
          </button>
          <label>
            Merchant
            <input
              value={bulkMerchant}
              maxLength={200}
              placeholder="Clean merchant name"
              onChange={(event) => setBulkMerchant(event.target.value)}
            />
          </label>
          <button type="button" onClick={() => void runBulk('merchant')}>
            Set merchant
          </button>
          <label>
            Savings transfer
            <select
              value={bulkSavingsEffect}
              onChange={(event) => setBulkSavingsEffect(event.target.value as SavingsEffect)}
            >
              <option value="CONTRIBUTION">Contribution</option>
              <option value="WITHDRAWAL">Withdrawal</option>
              <option value="NONE">Ordinary transfer</option>
            </select>
          </label>
          {bulkSavingsEffect !== 'NONE' && (
            <label>
              Savings category
              <select
                value={bulkSavingsSubcategory}
                onChange={(event) => setBulkSavingsSubcategory(event.target.value)}
              >
                <option value="">Choose…</option>
                {savingsSubcategories.map((cat) => (
                  <option key={cat.id} value={cat.id}>{cat.name}</option>
                ))}
              </select>
            </label>
          )}
          <button
            type="button"
            onClick={() => void runBulk('savings_effect')}
            disabled={bulkSavingsEffect !== 'NONE' && !bulkSavingsSubcategory}
          >
            Set transfer
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
          <svg className="empty-state-illustration" aria-hidden="true" width="96" height="68" viewBox="0 0 96 68" fill="none">
            {mode === 'review' ? (
              <>
                <circle cx="48" cy="34" r="24" fill="var(--accent-soft)" stroke="var(--accent)" strokeWidth="1.75" />
                <path d="M38 34.5l6.5 6.5 14-14.5" stroke="var(--accent)" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
                <circle cx="75" cy="16" r="4" fill="var(--chart-wants)" />
                <circle cx="20" cy="48" r="3" fill="var(--chart-needs)" />
              </>
            ) : (
              <>
                <rect x="16" y="14" width="52" height="38" rx="8" fill="var(--surface-alt)" stroke="var(--border-strong)" strokeWidth="1.6" />
                <path d="M26 26h28M26 34h20M26 42h14" stroke="var(--muted)" strokeWidth="2" strokeLinecap="round" />
                <circle cx="66" cy="42" r="14" fill="var(--accent-soft)" stroke="var(--accent)" strokeWidth="1.75" />
                <path d="M76 52l7 7" stroke="var(--accent)" strokeWidth="2.4" strokeLinecap="round" />
              </>
            )}
          </svg>
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
                  allSubcategories={allSubcategories}
                  onSelect={() => setSelected((current) => current.includes(item.id)
                    ? current.filter((id) => id !== item.id)
                    : [...current, item.id])}
                  onEdit={() => beginEdit(item)}
                  onCancel={() => { setEditingId(null); setDraft(null) }}
                  onDraft={(value) => setDraft({
                    ...value,
                    rememberRule: value.rememberRule && canRememberOutcome(
                      value, allSubcategories.find((category) => category.id === Number(value.subcategory)),
                    ),
                  })}
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
  allSubcategories: Subcategory[]
  onSelect: () => void
  onEdit: () => void
  onCancel: () => void
  onDraft: (draft: Draft) => void
  onSave: (event: FormEvent) => void
  onCreateSubcategory: (
    id: number,
    name: string,
    bucket: Bucket | null,
    classification: Classification,
    savings_effect: SavingsEffect,
  ) => Promise<Subcategory>
  reviewStatusLabel: string
  onMarkReviewed: () => void
  onResolve: (resolution: 'DISTINCT' | 'CONFIRMED_DUPLICATE', candidateId?: number) => void
}

function MerchantAvatar({ item }: { item: Transaction }) {
  const label = `${item.subcategory_name ?? ''} ${item.merchant || item.raw_description}`.toLowerCase()
  const isIncome = item.classification === 'INCOME' || item.classification === 'OTHER_INFLOW'
  const isTransfer = item.classification === 'TRANSFER'
  const isRefund = item.classification === 'REFUND'
  const isSavings = item.budget_bucket === 'SAVINGS' || item.savings_effect !== 'NONE'
  const isNeeds = item.budget_bucket === 'NEEDS'
  const isWants = item.budget_bucket === 'WANTS'

  const toneClass = isSavings
    ? 'merchant-avatar--savings'
    : isNeeds
      ? 'merchant-avatar--needs'
      : isWants
        ? 'merchant-avatar--wants'
        : isIncome || isRefund
          ? 'merchant-avatar--income'
          : isTransfer
            ? 'merchant-avatar--transfer'
            : 'merchant-avatar--default'

  const strokeColor = isSavings
    ? 'var(--chart-savings)'
    : isNeeds
      ? 'var(--chart-needs)'
      : isWants
        ? 'var(--chart-wants)'
        : isIncome || isRefund
          ? 'var(--positive)'
          : 'var(--accent)'

  const fillColor = isSavings
    ? 'var(--accent-soft)'
    : isNeeds
      ? 'var(--info-soft)'
      : isWants
        ? 'var(--warning-soft)'
        : isIncome || isRefund
          ? 'var(--positive-soft)'
          : 'var(--surface-alt)'

  return (
    <span
      className={`merchant-avatar ${toneClass}`}
      aria-hidden="true"
      style={{ background: fillColor, color: strokeColor }}
    >
      <svg width="16" height="16" viewBox="0 0 20 20" fill="none">
        {isIncome ? (
          <path d="M4 13.5l4-4 3 3 5-5.5M12 7h4v4" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" />
        ) : isRefund ? (
          <path d="M5 9.5h8.5a3 3 0 1 1 0 6H9M5 9.5L8 6.5M5 9.5l3 3" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" />
        ) : isSavings ? (
          <>
            <path d="M10 3l6 2.25v4.5c0 3.9-2.55 6.6-6 7.75-3.45-1.15-6-3.85-6-7.75v-4.5L10 3z" stroke="currentColor" strokeWidth="1.65" strokeLinejoin="round" />
            <path d="M7.5 10.25l1.75 1.75 3.5-3.75" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" />
          </>
        ) : isTransfer ? (
          <path d="M4.5 7.5h11m0 0-2.75-2.75M15.5 7.5l-2.75 2.75M15.5 12.5h-11m0 0 2.75-2.75M4.5 12.5l2.75 2.75" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" />
        ) : /grocer|market|food|coffee|cafe|dining|restaurant/.test(label) ? (
          <>
            <path d="M4 5.5h2l1.6 7h7.2l1.4-5H6.5" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" />
            <circle cx="8.25" cy="15.25" r="1.25" fill="currentColor" />
            <circle cx="13.75" cy="15.25" r="1.25" fill="currentColor" />
          </>
        ) : /rent|mortgag|home|hous|util|electric|water|power|internet/.test(label) ? (
          <path d="M3.5 9.5L10 4l6.5 5.5v6a1 1 0 0 1-1 1h-11a1 1 0 0 1-1-1v-6z" stroke="currentColor" strokeWidth="1.65" strokeLinejoin="round" />
        ) : /gas|uber|lyft|Auto|transit|train|flight|air|park/.test(label) ? (
          <>
            <path d="M4.5 11l1.5-4.5h8L15.5 11v3.5h-11V11z" stroke="currentColor" strokeWidth="1.65" strokeLinejoin="round" />
            <circle cx="7" cy="12.5" r="1" fill="currentColor" />
            <circle cx="13" cy="12.5" r="1" fill="currentColor" />
          </>
        ) : (
          <>
            <rect x="3.5" y="5" width="13" height="10" rx="2" stroke="currentColor" strokeWidth="1.65" />
            <path d="M3.5 8.5h13M6.5 12h3" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" />
          </>
        )}
      </svg>
    </span>
  )
}

function FragmentRow(props: RowProps) {
  const { item, mode, columnCount, selected, editing, draft, subcategories, allSubcategories, reviewStatusLabel } = props
  const reviewing = mode === 'review' && item.review_status === 'PENDING'
  const [candidateId, setCandidateId] = useState<number | ''>(item.duplicate_candidates[0]?.id ?? '')
  const [createName, setCreateName] = useState('')
  const [createBucket, setCreateBucket] = useState<Bucket>('NEEDS')
  const [createError, setCreateError] = useState('')
  const [creating, setCreating] = useState(false)
  const eligibleSubcategories = draft
    ? subcategories.filter((subcategory) => acceptsSubcategory(draft.classification, subcategory))
    : []
  const assignableBuckets = draft && !(draft.classification === 'TRANSFER' && draft.savings_effect === 'NONE')
    ? eligibleBuckets(draft.classification) : []
  const selectedSubcategory = draft
    ? allSubcategories.find((category) => category.id === Number(draft.subcategory))
    : undefined
  const retainedSubcategory = draft && draft.classification === item.classification
    && draft.subcategory === String(item.subcategory)
    && selectedSubcategory && !eligibleSubcategories.some((category) => category.id === selectedSubcategory.id)
    ? selectedSubcategory : undefined
  const outcomeNeedsSubcategory = draft
    ? (draft.classification === 'EXPENSE' || draft.classification === 'REFUND'
      || (draft.classification === 'TRANSFER' && draft.savings_effect !== 'NONE')) && !selectedSubcategory
    : false
  const ruleProblem = draft ? ruleIneligibility(draft, selectedSubcategory, item) : null

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
          <div className="merchant-cell">
            <MerchantAvatar item={item} />
            <div className="description">
              <span>{item.merchant || item.raw_description}</span>
              {item.merchant && <small>{item.raw_description}</small>}
            </div>
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
            <small className="rule-provenance">Automatically resolved · Rule #{item.categorized_by_rule}</small>
          )}
          {item.savings_effect !== 'NONE' && (
            <small className="review-reasons">Savings {item.savings_effect.toLowerCase()}</small>
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
          {reviewing && (
            <button type="button" onClick={props.onMarkReviewed}>Mark reviewed</button>
          )}
        </td>
      </tr>
      {editing && draft && (
        <tr className="detail-row">
          <td colSpan={columnCount}>
            <form className="transaction-editor" onSubmit={(event) => {
              if (reviewing && draft.rememberRule && ruleProblem) {
                event.preventDefault()
                return
              }
              props.onSave(event)
            }}>
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
                      savings_effect: 'NONE',
                      subcategory: nextSubcategory && acceptsSubcategory(classification, nextSubcategory)
                        ? draft.subcategory
                        : '',
                    })
                  }}
                >
                  {classifications.map((classification) => (
                    <option key={classification} value={classification}>{classificationLabels[classification]}</option>
                  ))}
                </select>
              </label>
              {draft.classification === 'TRANSFER' && (
                <label>
                  Savings activity
                  <select
                    value={draft.savings_effect}
                    onChange={(event) => props.onDraft({
                      ...draft,
                      savings_effect: event.target.value as SavingsEffect,
                      subcategory: event.target.value === 'NONE' || retainedSubcategory ? '' : draft.subcategory,
                    })}
                  >
                    <option value="NONE">Not savings</option>
                    <option value="CONTRIBUTION">Contribution</option>
                    <option value="WITHDRAWAL">Withdrawal</option>
                  </select>
                </label>
              )}
              {draft.classification === 'TRANSFER' && draft.savings_effect === 'NONE' && draft.subcategory && (
                <div>
                  <small>Existing Savings subcategory is retained until you remove it.</small>{' '}
                  <button type="button" onClick={() => props.onDraft({ ...draft, subcategory: '' })}>
                    Remove subcategory
                  </button>
                </div>
              )}
              {(draft.classification !== 'TRANSFER' || draft.savings_effect !== 'NONE') && <label>
                Subcategory
                <select
                  value={draft.subcategory}
                  required={draft.classification === 'TRANSFER'}
                  onChange={(event) => props.onDraft({
                    ...draft,
                    subcategory: event.target.value,
                  })}
                >
                  <option value="">No subcategory</option>
                  {retainedSubcategory && (
                    <option value={retainedSubcategory.id} disabled>
                      {retainedSubcategory.name} · retained {retainedSubcategory.is_active ? 'historical' : 'archived'}
                    </option>
                  )}
                  {eligibleSubcategories.map((subcategory) => (
                    <option key={subcategory.id} value={subcategory.id}>
                      {subcategory.name} · {subcategory.budget_bucket
                        ? bucketLabels[subcategory.budget_bucket]
                        : 'Other Inflows'}
                    </option>
                  ))}
                </select>
              </label>}
              {reviewing && (assignableBuckets.length > 0 || draft.classification === 'OTHER_INFLOW') && (
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
                    {draft.classification !== 'OTHER_INFLOW' && <label>
                      Bucket
                      <select
                        value={assignableBuckets.includes(createBucket) ? createBucket : assignableBuckets[0]}
                        onChange={(event) => setCreateBucket(event.target.value as Bucket)}
                      >
                        {assignableBuckets.map((value) => (
                          <option key={value} value={value}>{bucketLabels[value]}</option>
                        ))}
                      </select>
                    </label>}
                    <button
                      className="secondary-button"
                      type="button"
                      disabled={!createName.trim() || creating}
                      onClick={async () => {
                        setCreating(true)
                        setCreateError('')
                        try {
                          const validBucket = draft.classification === 'OTHER_INFLOW'
                            ? null
                            : assignableBuckets.includes(createBucket)
                              ? createBucket
                              : assignableBuckets[0]
                          await props.onCreateSubcategory(
                            item.id,
                            createName,
                            validBucket,
                            draft.classification,
                            draft.savings_effect,
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
              {reviewing && item.categorization_rule_conflict && (
                <label className="remember-rule-toggle">
                  <input
                    type="checkbox"
                    checked={draft.resolveConflict}
                    onChange={(event) => props.onDraft({ ...draft, resolveConflict: event.target.checked })}
                  />
                  Resolve this rule conflict with the outcome above
                </label>
              )}
              {reviewing && draft.classification !== 'UNKNOWN' && !outcomeNeedsSubcategory && (
                <label className="remember-rule-toggle">
                  <input
                    type="checkbox"
                    checked={draft.confirmOutcome}
                    onChange={(event) => props.onDraft({ ...draft, confirmOutcome: event.target.checked })}
                  />
                  Confirm this financial outcome
                </label>
              )}
              {reviewing && canRememberOutcome(draft, selectedSubcategory) && (
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
                    Remember this outcome for future matching transactions
                  </label>
                  {draft.rememberRule && (
                    <>
                      <label>
                        Raw description
                        <select
                          value={draft.ruleMatchType}
                          onChange={(event) => props.onDraft({
                            ...draft,
                            ruleMatchType: event.target.value as Draft['ruleMatchType'],
                          })}
                        >
                          <option value="CONTAINS">contains</option>
                          <option value="STARTS_WITH">starts with</option>
                        </select>
                      </label>
                      <label>
                        Match value (whitespace collapsed when saved)
                        <input
                          value={draft.ruleMatchValue}
                          maxLength={200}
                          required
                          onChange={(event) => props.onDraft({ ...draft, ruleMatchValue: event.target.value })}
                        />
                      </label>
                      <p>
                        When {draft.classification === 'EXPENSE'
                          ? 'an Unknown transaction or an uncategorized Expense'
                          : draft.classification === 'TRANSFER' && draft.savings_effect !== 'NONE'
                            ? 'an Unknown transaction or unresolved Transfer'
                            : 'an Unknown transaction'} description {draft.ruleMatchType === 'STARTS_WITH' ? 'starts with' : 'contains'}
                        {' '}“{normalizedRuleValue(draft.ruleMatchValue) || '…'}”, set{' '}
                        {classificationLabels[draft.classification]}
                        {draft.savings_effect !== 'NONE' ? ` · Savings ${draft.savings_effect.toLowerCase()}` : ''}
                        {selectedSubcategory ? ` · ${selectedSubcategory.name}` : ''}.
                      </p>
                      <p className={ruleProblem ? 'inline-create-error' : ''}>
                        {ruleProblem ?? 'Eligible: this outcome can be saved as a rule.'}
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

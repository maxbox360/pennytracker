import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { apiFetch } from './api'

type Bucket = 'NEEDS' | 'WANTS' | 'SAVINGS'
type Classification = 'INCOME' | 'EXPENSE' | 'REFUND' | 'OTHER_INFLOW' | 'TRANSFER' | 'EXCLUDED'
type Subcategory = {
  id: number
  name: string
  scope: 'BUDGET' | 'OTHER_INFLOW'
  budget_bucket: Bucket | null
  is_active: boolean
}
type Rule = {
  id: number
  name: string
  match_field: 'RAW_DESCRIPTION'
  match_type: MatchType
  match_value: string
  classification: Classification
  savings_effect: SavingsEffect
  allow_unknown: boolean
  subcategory: number | null
  subcategory_name: string | null
  subcategory_bucket: Bucket | null
  subcategory_is_active: boolean | null
  is_active: boolean
}
type MatchType = 'CONTAINS' | 'STARTS_WITH'
type SavingsEffect = 'NONE' | 'CONTRIBUTION' | 'WITHDRAWAL'
type Draft = {
  name: string
  classification: Classification
  match_type: MatchType
  match_value: string
  savings_effect: SavingsEffect
  subcategory: string
  allow_unknown: boolean
}
const emptyDraft: Draft = {
  name: '',
  classification: 'EXPENSE',
  match_type: 'CONTAINS',
  match_value: '',
  savings_effect: 'NONE',
  subcategory: '',
  allow_unknown: true,
}
const classificationLabels: Record<Classification, string> = {
  INCOME: 'Income',
  EXPENSE: 'Expense',
  REFUND: 'Refund',
  OTHER_INFLOW: 'Other inflow',
  TRANSFER: 'Transfer',
  EXCLUDED: 'Excluded',
}
const matchLabels: Record<MatchType, string> = { CONTAINS: 'contains', STARTS_WITH: 'starts with' }
const effectLabels = { NONE: 'None', CONTRIBUTION: 'Contribution', WITHDRAWAL: 'Withdrawal' } as const

function subcategoryFits(classification: Classification, category: Pick<Subcategory, 'scope' | 'budget_bucket'>) {
  if (classification === 'OTHER_INFLOW') return category.scope === 'OTHER_INFLOW'
  if (category.scope !== 'BUDGET') return false
  if (classification === 'EXPENSE' || classification === 'REFUND') {
    return category.budget_bucket === 'NEEDS' || category.budget_bucket === 'WANTS'
  }
  return classification === 'TRANSFER' && category.budget_bucket === 'SAVINGS'
}

function subcategoryRequired(draft: Pick<Draft, 'classification' | 'savings_effect'>) {
  return draft.classification === 'EXPENSE' || draft.classification === 'REFUND'
    || (draft.classification === 'TRANSFER' && draft.savings_effect !== 'NONE')
}

function ruleSentence(
  rule: Pick<Rule, 'classification' | 'match_type' | 'match_value' | 'savings_effect' | 'allow_unknown'>,
  target: string | null,
) {
  const outcome = [
    `classify it as ${classificationLabels[rule.classification]}`,
    rule.savings_effect !== 'NONE' ? `mark a Savings ${effectLabels[rule.savings_effect].toLowerCase()}` : '',
    target ? `assign ${target}` : '',
  ].filter(Boolean).join(', ')
  const scope = rule.classification === 'EXPENSE'
    ? (rule.allow_unknown ? ' (applies to Unknown transactions and existing Expenses)' : ' (applies to transactions already classified as Expense)')
    : rule.classification === 'TRANSFER' && rule.savings_effect !== 'NONE'
      ? ' (applies to Unknown transactions and unresolved Transfers)'
      : ' (applies to Unknown transactions)'
  return `When a raw description ${matchLabels[rule.match_type]} “${rule.match_value}”, ${outcome}${scope}.`
}

function ruleUsable(rule: Rule) {
  if (rule.subcategory === null) return !subcategoryRequired(rule)
  return Boolean(rule.subcategory_is_active)
    && subcategoryFits(rule.classification, {
      scope: rule.subcategory_bucket ? 'BUDGET' : 'OTHER_INFLOW',
      budget_bucket: rule.subcategory_bucket,
    })
}

function errorText(value: unknown): string {
  if (typeof value === 'string') return value
  if (value && typeof value === 'object') return JSON.stringify(value)
  return 'The request failed.'
}

const bucketLabels: Record<Bucket, string> = {
  NEEDS: 'Needs',
  WANTS: 'Wants',
  SAVINGS: 'Savings',
}

function normalizedRuleValue(value: string) {
  return value.replace(/\s+/g, ' ').trim()
}

export default function Rules({ reload, onChanged }: { reload: number; onChanged: () => void }) {
  const [rules, setRules] = useState<Rule[]>([])
  const [categories, setCategories] = useState<Subcategory[]>([])
  const [editingId, setEditingId] = useState<number | null>(null)
  const [formOpen, setFormOpen] = useState(false)
  const [draft, setDraft] = useState<Draft>(emptyDraft)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [pendingPrompt, setPendingPrompt] = useState<{
    id: number
    matching: number
    conflicting: number
  } | null>(null)

  async function load() {
    const [ruleResponse, categoryResponse] = await Promise.all([
      apiFetch('/api/categorization-rules/'),
      apiFetch('/api/subcategories/'),
    ])
    const [ruleResult, categoryResult] = await Promise.all([
      ruleResponse.json(),
      categoryResponse.json(),
    ])
    if (!ruleResponse.ok) throw new Error(errorText(ruleResult))
    if (!categoryResponse.ok) throw new Error(errorText(categoryResult))
    setRules(ruleResult as Rule[])
    setCategories(categoryResult as Subcategory[])
  }

  useEffect(() => {
    load().catch((loadError: unknown) => setError(
      loadError instanceof Error ? loadError.message : 'Unable to load rules',
    ))
  }, [reload])

  function beginEdit(rule?: Rule) {
    setError('')
    setNotice('')
    setEditingId(rule?.id ?? null)
    setFormOpen(true)
    setDraft(rule
      ? {
        name: rule.name,
        classification: rule.classification,
        match_type: rule.match_type,
        match_value: rule.match_value,
        savings_effect: rule.savings_effect,
        subcategory: rule.subcategory === null ? '' : String(rule.subcategory),
        allow_unknown: rule.allow_unknown,
      }
      : emptyDraft)
  }

  async function save(event: FormEvent) {
    event.preventDefault()
    setError('')
    setNotice('')
    try {
      const response = await apiFetch(
        editingId === null
          ? '/api/categorization-rules/'
          : `/api/categorization-rules/${editingId}/`,
        {
          method: editingId === null ? 'POST' : 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name: draft.name,
            match_field: 'RAW_DESCRIPTION',
            match_type: draft.match_type,
            match_value: draft.match_value,
            classification: draft.classification,
            savings_effect: draft.classification === 'TRANSFER' ? draft.savings_effect : 'NONE',
            subcategory: draft.subcategory ? Number(draft.subcategory) : null,
            allow_unknown: draft.classification === 'EXPENSE' ? draft.allow_unknown : false,
            ...(editingId === null ? { is_active: true } : {}),
          }),
        },
      )
      const result = await response.json()
      if (!response.ok) throw new Error(errorText(result))
      const saved = result as Rule
      setEditingId(null)
      setFormOpen(false)
      setNotice('Rule saved. Existing transactions were not changed.')
      await load()
      onChanged()
      await checkPending(saved.id)
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : 'Unable to save rule')
    }
  }

  async function checkPending(id: number) {
    try {
      const response = await apiFetch(`/api/categorization-rules/${id}/pending-matches/`)
      const result = await response.json()
      if (!response.ok) throw new Error(errorText(result))
      if (result.matching_pending_count > 0 || result.conflicting_pending_count > 0) {
        setPendingPrompt({
          id,
          matching: result.matching_pending_count,
          conflicting: result.conflicting_pending_count,
        })
      } else {
        setPendingPrompt(null)
      }
    } catch (checkError) {
      setError(checkError instanceof Error ? checkError.message : 'Unable to check pending matches')
    }
  }

  async function applyPending() {
    if (!pendingPrompt) return
    setError('')
    try {
      const response = await apiFetch(
        `/api/categorization-rules/${pendingPrompt.id}/apply-to-pending/`,
        { method: 'POST' },
      )
      const result = await response.json()
      if (!response.ok) throw new Error(errorText(result))
      setNotice(
        `Applied the rule to ${result.updated_count} transactions.`
        + (result.conflicting_count ? ` ${result.conflicting_count} conflicting transactions remain in Review.` : ''),
      )
      setPendingPrompt(null)
      await load()
      onChanged()
    } catch (applyError) {
      setError(applyError instanceof Error ? applyError.message : 'Unable to apply rule')
    }
  }

  async function updateRule(rule: Rule, body: object, success: string) {
    setError('')
    try {
      const response = await apiFetch(`/api/categorization-rules/${rule.id}/`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      const result = await response.json()
      if (!response.ok) throw new Error(errorText(result))
      setNotice(success)
      await load()
      onChanged()
      if ('match_value' in body || 'subcategory' in body) await checkPending(rule.id)
    } catch (updateError) {
      setError(updateError instanceof Error ? updateError.message : 'Unable to update rule')
    }
  }

  async function removeRule(rule: Rule) {
    setError('')
    try {
      const response = await apiFetch(`/api/categorization-rules/${rule.id}/`, { method: 'DELETE' })
      if (!response.ok) {
        const result = await response.json()
        throw new Error(errorText(result))
      }
      setNotice('Rule removed.')
      await load()
      onChanged()
    } catch (removeError) {
      setError(removeError instanceof Error ? removeError.message : 'Unable to remove rule')
    }
  }

  const assignable = categories.filter((category) => (
    category.is_active && subcategoryFits(draft.classification, category)
  ))
  const currentUnavailable = categories.find((category) => (
    category.id === Number(draft.subcategory)
    && (!category.is_active || !subcategoryFits(draft.classification, category))
  ))
  const targetName = draft.subcategory
    ? categories.find((category) => category.id === Number(draft.subcategory))?.name ?? null
    : null
  const canSubcategorize = draft.classification !== 'INCOME' && draft.classification !== 'EXCLUDED'
    && (draft.classification !== 'TRANSFER' || draft.savings_effect !== 'NONE')

  return (
    <section className="rules-panel" aria-labelledby="rules-heading">
      <header className="categories-header">
        <div>
          <h2 id="rules-heading">Rules</h2>
          <p>
            Rules set a classification, optional Savings effect, and subcategory for matching raw descriptions.
            Rules apply only to eligible unresolved transactions; Expense rules can categorize uncategorized Expenses.
            Matching is case-insensitive and deterministic.
          </p>
        </div>
        <button className="primary-button" type="button" onClick={() => beginEdit()}>Create rule</button>
      </header>

      {error && <p className="message message--error" role="alert">{error}</p>}
      {notice && <p className="table-notice" role="status">{notice}</p>}
      {pendingPrompt && (
        <div className="rule-apply-prompt" role="status">
          <span>
            {pendingPrompt.matching > 0
              ? `${pendingPrompt.matching} other pending transaction${pendingPrompt.matching === 1 ? '' : 's'} match this rule. Apply it?`
              : 'No compatible pending transactions can be applied.'}
            {pendingPrompt.conflicting > 0
              ? ` ${pendingPrompt.conflicting} conflicting match${pendingPrompt.conflicting === 1 ? '' : 'es'} will remain in Review.`
              : ''}
          </span>
          {pendingPrompt.matching > 0 && (
            <button type="button" className="primary-button" onClick={() => void applyPending()}>
              Apply to matching pending transactions
            </button>
          )}
          <button type="button" className="quiet-button" onClick={() => setPendingPrompt(null)}>Not now</button>
        </div>
      )}

      {formOpen && (
        <form className="rule-editor" onSubmit={(event) => void save(event)}>
          <label>
            Rule name (optional)
            <input value={draft.name} maxLength={100} onChange={(event) => setDraft({ ...draft, name: event.target.value })} />
          </label>
          <label>
            Classification
            <select
              value={draft.classification}
              onChange={(event) => setDraft({
                ...draft,
                classification: event.target.value as Classification,
                savings_effect: 'NONE',
                subcategory: '',
              })}
            >
              {(Object.keys(classificationLabels) as Classification[]).map((value) => (
                <option key={value} value={value}>{classificationLabels[value]}</option>
              ))}
            </select>
          </label>
          <label>
            Raw description
            <select
              value={draft.match_type}
              onChange={(event) => setDraft({ ...draft, match_type: event.target.value as MatchType })}
            >
              <option value="CONTAINS">contains</option>
              <option value="STARTS_WITH">starts with</option>
            </select>
          </label>
          <label>
            Match value (whitespace collapsed when saved)
            <input
              value={draft.match_value}
              maxLength={200}
              required
              placeholder="COFFEE SHOP"
              onChange={(event) => setDraft({ ...draft, match_value: event.target.value })}
            />
          </label>
          {draft.classification === 'TRANSFER' && (
            <label>
              Savings effect
              <select
                value={draft.savings_effect}
                onChange={(event) => setDraft({
                  ...draft,
                  savings_effect: event.target.value as SavingsEffect,
                  subcategory: event.target.value === 'NONE' ? '' : draft.subcategory,
                })}
              >
                <option value="NONE">None</option>
                <option value="CONTRIBUTION">Contribution</option>
                <option value="WITHDRAWAL">Withdrawal</option>
              </select>
            </label>
          )}
          {draft.classification === 'EXPENSE' && (
            <label className="remember-rule-toggle">
              <input
                type="checkbox"
                checked={draft.allow_unknown}
                onChange={(event) => setDraft({ ...draft, allow_unknown: event.target.checked })}
              />
              Apply to Unknown transactions (otherwise only existing Expenses)
            </label>
          )}
          {canSubcategorize && <label>
            Subcategory{subcategoryRequired(draft) ? '' : ' (optional)'}
            <select
              value={draft.subcategory}
              required={subcategoryRequired(draft)}
              onChange={(event) => setDraft({ ...draft, subcategory: event.target.value })}
            >
              <option value="">{subcategoryRequired(draft) ? 'Choose…' : 'None'}</option>
              {currentUnavailable && (
                <option value={currentUnavailable.id}>
                  {currentUnavailable.name} · {currentUnavailable.budget_bucket ? bucketLabels[currentUnavailable.budget_bucket] : 'Other Inflows'}
                  {currentUnavailable.is_active ? ' (not valid for this outcome)' : ' (archived)'}
                </option>
              )}
              {assignable.map((category) => (
                <option key={category.id} value={category.id}>
                  {category.name} · {category.budget_bucket ? bucketLabels[category.budget_bucket] : 'Other Inflows'}
                </option>
              ))}
            </select>
          </label>}
          <p className="rule-preview">
            {ruleSentence({ ...draft, match_value: normalizedRuleValue(draft.match_value) || '…' }, targetName)}
          </p>
          <div className="editor-actions">
            <button className="primary-button" type="submit">Save rule</button>
            <button
              className="refresh-button"
              type="button"
              onClick={() => {
                setFormOpen(false)
                setEditingId(null)
              }}
            >
              Cancel
            </button>
          </div>
        </form>
      )}

      {rules.length === 0 ? (
        <p className="message">No transaction rules yet.</p>
      ) : (
        <div className="rule-list">
          {rules.map((rule) => (
            <article className={`rule-row ${rule.is_active ? '' : 'rule-row--disabled'}`} key={rule.id}>
              <div className="rule-condition">
                <strong>
                  Rule #{rule.id} · {rule.name || `Description ${matchLabels[rule.match_type]} “${rule.match_value}”`}
                  {' '}<small>{classificationLabels[rule.classification]}{rule.savings_effect !== 'NONE' ? ` · Savings ${rule.savings_effect.toLowerCase()}` : ''}</small>
                </strong>
                <span>{ruleSentence(rule, rule.subcategory_name)}</span>
              </div>
              <div className="rule-target">
                <span>{rule.subcategory_name ? `${rule.subcategory_name} · ${rule.subcategory_bucket ? bucketLabels[rule.subcategory_bucket] : 'Other Inflows'}` : 'No subcategory'}</span>
                {!ruleUsable(rule) && (
                  <small className="rule-warning">
                    {rule.subcategory !== null && !rule.subcategory_is_active
                      ? 'Target subcategory is archived; this rule is not applied.'
                      : 'Target subcategory is not valid for this outcome; this rule is not applied.'}
                  </small>
                )}
              </div>
              <span className={`category-state ${rule.is_active ? '' : 'category-state--archived'}`}>
                {!rule.is_active
                  ? 'Disabled'
                  : !ruleUsable(rule)
                    ? 'Unusable'
                    : 'Active'}
              </span>
              <div className="rule-actions">
                <button type="button" onClick={() => beginEdit(rule)}>Edit</button>
                <button
                  type="button"
                  className="quiet-button"
                  onClick={() => void updateRule(
                    rule,
                    { is_active: !rule.is_active },
                    rule.is_active ? 'Rule disabled; existing transactions were unchanged.' : 'Rule enabled.',
                  )}
                >
                  {rule.is_active ? 'Disable' : 'Enable'}
                </button>
                <button type="button" className="quiet-button" onClick={() => void removeRule(rule)}>Remove</button>
              </div>
            </article>
          ))}
        </div>
      )}
    </section>
  )
}

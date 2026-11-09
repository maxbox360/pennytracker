import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { apiFetch } from './api'

type Bucket = 'NEEDS' | 'WANTS' | 'SAVINGS'
type Subcategory = { id: number; name: string; budget_bucket: Bucket; is_active: boolean }
type Rule = {
  id: number
  name: string
  match_field: 'RAW_DESCRIPTION'
  match_type: MatchType
  match_value: string
  rule_kind: RuleKind
  classification: 'EXPENSE' | 'TRANSFER'
  savings_effect: SavingsEffect
  subcategory: number
  subcategory_name: string
  subcategory_bucket: Bucket
  subcategory_is_active: boolean
  is_active: boolean
}
type RuleKind = 'EXPENSE_CATEGORY' | 'SAVINGS_TRANSFER'
type MatchType = 'CONTAINS' | 'STARTS_WITH'
type SavingsEffect = 'NONE' | 'CONTRIBUTION' | 'WITHDRAWAL'
type Draft = {
  name: string
  rule_kind: RuleKind
  match_type: MatchType
  match_value: string
  savings_effect: 'CONTRIBUTION' | 'WITHDRAWAL'
  subcategory: string
}
const emptyDraft: Draft = {
  name: '',
  rule_kind: 'EXPENSE_CATEGORY',
  match_type: 'CONTAINS',
  match_value: '',
  savings_effect: 'CONTRIBUTION',
  subcategory: '',
}
const targetBuckets: Record<RuleKind, Bucket[]> = {
  EXPENSE_CATEGORY: ['NEEDS', 'WANTS'],
  SAVINGS_TRANSFER: ['SAVINGS'],
}
const matchLabels: Record<MatchType, string> = { CONTAINS: 'contains', STARTS_WITH: 'starts with' }
const effectLabels = { CONTRIBUTION: 'Contribution', WITHDRAWAL: 'Withdrawal' } as const

function ruleSentence(rule: Pick<Rule, 'rule_kind' | 'match_type' | 'match_value' | 'savings_effect'>, target: string) {
  const condition = `When a raw description ${matchLabels[rule.match_type]} “${rule.match_value}”`
  return rule.rule_kind === 'SAVINGS_TRANSFER'
    ? `${condition}, classify it as a Transfer, mark it as a Savings ${effectLabels[rule.savings_effect as 'CONTRIBUTION'].toLowerCase()}, and assign ${target}.`
    : `${condition} on an Expense, assign ${target}. Classification is not changed.`
}

function ruleUsable(rule: Rule) {
  return rule.subcategory_is_active && targetBuckets[rule.rule_kind].includes(rule.subcategory_bucket)
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
        rule_kind: rule.rule_kind,
        match_type: rule.match_type,
        match_value: rule.match_value,
        savings_effect: rule.savings_effect === 'WITHDRAWAL' ? 'WITHDRAWAL' : 'CONTRIBUTION',
        subcategory: String(rule.subcategory),
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
            rule_kind: draft.rule_kind,
            savings_effect: draft.rule_kind === 'SAVINGS_TRANSFER' ? draft.savings_effect : 'NONE',
            subcategory: draft.subcategory,
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

  const allowedBuckets = targetBuckets[draft.rule_kind]
  const assignable = categories.filter((category) => (
    category.is_active && allowedBuckets.includes(category.budget_bucket)
  ))
  const currentUnavailable = categories.find((category) => (
    category.id === Number(draft.subcategory)
    && (!category.is_active || !allowedBuckets.includes(category.budget_bucket))
  ))
  const targetName = categories.find((category) => category.id === Number(draft.subcategory))?.name
    || 'the selected subcategory'

  return (
    <section className="rules-panel" aria-labelledby="rules-heading">
      <header className="categories-header">
        <div>
          <h2 id="rules-heading">Rules</h2>
          <p>
            Expense categorization rules assign a Needs/Wants subcategory to transactions that are already Expenses and never change classification.
            Savings transfer rules explicitly classify a matching unresolved transaction as a Transfer with a Savings effect.
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
            Rule type
            <select
              value={draft.rule_kind}
              onChange={(event) => setDraft({ ...draft, rule_kind: event.target.value as RuleKind, subcategory: '' })}
            >
              <option value="EXPENSE_CATEGORY">Expense categorization</option>
              <option value="SAVINGS_TRANSFER">Savings transfer (sets classification)</option>
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
              placeholder={draft.rule_kind === 'SAVINGS_TRANSFER' ? 'AUTO SAVE *' : 'COFFEE SHOP'}
              onChange={(event) => setDraft({ ...draft, match_value: event.target.value })}
            />
          </label>
          {draft.rule_kind === 'SAVINGS_TRANSFER' && (
            <label>
              Savings effect
              <select
                value={draft.savings_effect}
                onChange={(event) => setDraft({ ...draft, savings_effect: event.target.value as 'CONTRIBUTION' | 'WITHDRAWAL' })}
              >
                <option value="CONTRIBUTION">Contribution</option>
                <option value="WITHDRAWAL">Withdrawal</option>
              </select>
            </label>
          )}
          <label>
            {draft.rule_kind === 'SAVINGS_TRANSFER' ? 'Target Savings subcategory' : 'Target subcategory'}
            <select
              value={draft.subcategory}
              required
              onChange={(event) => setDraft({ ...draft, subcategory: event.target.value })}
            >
              <option value="">Choose…</option>
              {currentUnavailable && (
                <option value={currentUnavailable.id}>
                  {currentUnavailable.name} · {bucketLabels[currentUnavailable.budget_bucket]}
                  {currentUnavailable.is_active ? ' (not valid for this rule type)' : ' (archived)'}
                </option>
              )}
              {assignable.map((category) => (
                <option key={category.id} value={category.id}>
                  {category.name} · {bucketLabels[category.budget_bucket]}
                </option>
              ))}
            </select>
          </label>
          <p className="rule-preview">
            {ruleSentence({ ...draft, match_value: normalizedRuleValue(draft.match_value) || '…' }, targetName)}
          </p>
          <p className="rule-example">
            {draft.rule_kind === 'SAVINGS_TRANSFER'
              ? 'Applies only to Unknown transactions and unresolved Transfers with no Savings effect. It never overwrites a resolved transaction.'
              : 'Applies only to transactions already classified as Expense.'}
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
        <p className="message">No categorization rules yet.</p>
      ) : (
        <div className="rule-list">
          {rules.map((rule) => (
            <article className={`rule-row ${rule.is_active ? '' : 'rule-row--disabled'}`} key={rule.id}>
              <div className="rule-condition">
                <strong>
                  Rule #{rule.id} · {rule.name || `Description ${matchLabels[rule.match_type]} “${rule.match_value}”`}
                  {' '}<small>{rule.rule_kind === 'SAVINGS_TRANSFER' ? 'Savings transfer' : 'Expense categorization'}</small>
                </strong>
                <span>{ruleSentence(rule, rule.subcategory_name)}</span>
              </div>
              <div className="rule-target">
                <span>{rule.subcategory_name} · {bucketLabels[rule.subcategory_bucket]}</span>
                {!ruleUsable(rule) && (
                  <small className="rule-warning">
                    {!rule.subcategory_is_active
                      ? 'Target subcategory is archived; this rule is not applied.'
                      : `Target bucket is not valid for this rule type (${rule.rule_kind === 'SAVINGS_TRANSFER' ? 'Savings' : 'Needs or Wants'} required); this rule is not applied.`}
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

import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { apiFetch } from './api'

type Bucket = 'NEEDS' | 'WANTS' | 'SAVINGS'
type Subcategory = { id: number; name: string; budget_bucket: Bucket; is_active: boolean }
type Rule = {
  id: number
  name: string
  match_field: 'RAW_DESCRIPTION'
  match_type: 'CONTAINS'
  match_value: string
  classification: 'EXPENSE'
  subcategory: number
  subcategory_name: string
  subcategory_bucket: Bucket
  subcategory_is_active: boolean
  is_active: boolean
}
type Draft = { name: string; match_value: string; subcategory: string }

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
  const [draft, setDraft] = useState<Draft>({ name: '', match_value: '', subcategory: '' })
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
      ? { name: rule.name, match_value: rule.match_value, subcategory: String(rule.subcategory) }
      : { name: '', match_value: '', subcategory: '' })
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
            match_type: 'CONTAINS',
            match_value: draft.match_value,
            classification: 'EXPENSE',
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

  const assignable = categories.filter((category) => (
    category.is_active && category.budget_bucket !== 'SAVINGS'
  ))
  const currentUnavailable = categories.find((category) => (
    category.id === Number(draft.subcategory)
    && (!category.is_active || category.budget_bucket === 'SAVINGS')
  ))

  return (
    <section className="rules-panel" aria-labelledby="rules-heading">
      <header className="categories-header">
        <div>
          <h2 id="rules-heading">Categorization rules</h2>
          <p>Active rules match Expense descriptions case-insensitively and assign a Needs/Wants subcategory. They never infer transaction classification.</p>
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
            Match value (whitespace collapsed when saved)
            <input
              value={draft.match_value}
              maxLength={200}
              required
              onChange={(event) => setDraft({ ...draft, match_value: event.target.value })}
            />
          </label>
          <label>
            Target subcategory
            <select
              value={draft.subcategory}
              required
              onChange={(event) => setDraft({ ...draft, subcategory: event.target.value })}
            >
              <option value="">Choose…</option>
              {currentUnavailable && (
                <option value={currentUnavailable.id}>
                  {currentUnavailable.name} · {bucketLabels[currentUnavailable.budget_bucket]}
                  {currentUnavailable.is_active ? ' (Needs/Wants required)' : ' (archived)'}
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
            When an EXPENSE raw description contains “{normalizedRuleValue(draft.match_value) || '…'}”,
            assign {categories.find((category) => category.id === Number(draft.subcategory))?.name || 'the selected subcategory'}.
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
                <strong>Rule #{rule.id} · {rule.name || `Description contains “${rule.match_value}”`}</strong>
                <span>EXPENSE + raw description contains “{rule.match_value}”</span>
              </div>
              <div className="rule-target">
                <span>{rule.subcategory_name} · {bucketLabels[rule.subcategory_bucket]}</span>
                {(!rule.subcategory_is_active || rule.subcategory_bucket === 'SAVINGS') && (
                  <small className="rule-warning">
                    {!rule.subcategory_is_active
                      ? 'Target subcategory is archived; this rule is not applied.'
                      : 'Target is Savings; expense rules require Needs or Wants and are not applied.'}
                  </small>
                )}
              </div>
              <span className={`category-state ${rule.is_active ? '' : 'category-state--archived'}`}>
                {!rule.is_active
                  ? 'Disabled'
                  : !rule.subcategory_is_active || rule.subcategory_bucket === 'SAVINGS'
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

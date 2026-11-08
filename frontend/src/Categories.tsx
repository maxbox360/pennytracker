import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { apiFetch } from './api'

type Bucket = 'NEEDS' | 'WANTS' | 'SAVINGS'
type Scope = 'BUDGET' | 'OTHER_INFLOW'
type Subcategory = { id: number; name: string; scope: Scope; budget_bucket: Bucket | null; is_active: boolean }
type Draft = { name: string; budget_bucket: Bucket | null }

const buckets: Bucket[] = ['NEEDS', 'WANTS', 'SAVINGS']
const labels: Record<Bucket, string> = { NEEDS: 'Needs', WANTS: 'Wants', SAVINGS: 'Savings' }

function errorText(value: unknown): string {
  if (typeof value === 'string') return value
  if (value && typeof value === 'object') return JSON.stringify(value)
  return 'The request failed.'
}

function bulkCreateError(value: unknown): string {
  if (value && typeof value === 'object') {
    const body = value as {
      detail?: unknown
      names?: unknown
      invalid?: { name?: unknown; message?: unknown }[]
    }
    if (Array.isArray(body.invalid) && body.invalid.length) {
      return body.invalid
        .map((item) => `${String(item.name || 'Subcategory')}: ${String(item.message || 'Invalid name.')}`)
        .join(' ')
    }
    if (typeof body.detail === 'string') return body.detail
    if (typeof body.names === 'string') return body.names
  }
  return 'Unable to create subcategories. Check the names and selected budget bucket.'
}

function joinNames(names: string[]): string {
  return names.length < 2 ? names.join('') : `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`
}

export default function Categories() {
  const [categories, setCategories] = useState<Subcategory[]>([])
  const [drafts, setDrafts] = useState<Record<number, Draft>>({})
  const [name, setName] = useState('')
  const [bucket, setBucket] = useState<Bucket>('NEEDS')
  const [scope, setScope] = useState<Scope>('BUDGET')
  const [showArchived, setShowArchived] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  async function load() {
    const response = await apiFetch('/api/subcategories/')
    const result = await response.json()
    if (!response.ok) throw new Error(errorText(result))
    setCategories(result as Subcategory[])
    setDrafts(Object.fromEntries((result as Subcategory[]).map((category) => [
      category.id,
      { name: category.name, budget_bucket: category.budget_bucket },
    ])))
  }

  useEffect(() => {
    load().catch((loadError: unknown) => setError(
      loadError instanceof Error ? loadError.message : 'Unable to load categories',
    ))
  }, [])

  async function save(path: string, method: 'POST' | 'PATCH', body: object, message: string) {
    setError('')
    setNotice('')
    try {
      const response = await apiFetch(path, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      const result = await response.json()
      if (!response.ok) throw new Error(errorText(result))
      await load()
      setNotice(message)
      return true
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : 'Unable to save category')
      return false
    }
  }

  async function createCategory(event: FormEvent) {
    event.preventDefault()
    setError('')
    setNotice('')
    try {
      const response = await apiFetch('/api/subcategories/bulk-create/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          names: name,
          scope,
          budget_bucket: scope === 'BUDGET' ? bucket : null,
        }),
      })
      const result = await response.json() as {
        count: number
        already_exists: string[]
        archived: string[]
        duplicate_count: number
        submitted_count: number
      }
      if (!response.ok) throw new Error(bulkCreateError(result))
      setName('')
      await load()
      const messages: string[] = []
      if (result.count) {
        messages.push(`${result.count} subcategor${result.count === 1 ? 'y' : 'ies'} created.`)
      } else if (
        result.already_exists.length === result.submitted_count
        && result.duplicate_count === 0
        && result.archived.length === 0
      ) {
        messages.push(`All ${result.submitted_count} subcategories already exist.`)
      } else {
        messages.push('No new subcategories were created.')
      }
      if (result.already_exists.length) {
        messages.push(result.count
          ? `${joinNames(result.already_exists)} already existed and were skipped.`
          : `Already active and skipped: ${joinNames(result.already_exists)}.`)
      }
      if (result.duplicate_count) {
        messages.push(`${result.duplicate_count} duplicate entr${result.duplicate_count === 1 ? 'y was' : 'ies were'} ignored.`)
      }
      if (result.archived.length === 1) {
        messages.push(`${result.archived[0]} already exists but is archived. Reactivate it from Categories to use it again.`)
      } else if (result.archived.length > 1) {
        messages.push(`${joinNames(result.archived)} are archived. Reactivate them from Categories to use them again.`)
      }
      setNotice(messages.join(' '))
    } catch (createError) {
      setError(createError instanceof Error ? createError.message : 'Unable to create subcategories')
    }
  }

  const visible = categories.filter((category) => showArchived || category.is_active)

  return (
    <section className="categories-panel" aria-labelledby="categories-heading">
      <header className="categories-header">
        <div>
          <h2 id="categories-heading">Subcategories</h2>
          <p>Moving a category changes future assignments. Existing transactions keep their historical classification.</p>
        </div>
        <label className="archived-toggle">
          <input type="checkbox" checked={showArchived} onChange={(event) => setShowArchived(event.target.checked)} />
          Show archived
        </label>
      </header>

      {error && <p className="message message--error" role="alert">{error}</p>}
      {notice && <p className="table-notice" role="status">{notice}</p>}

      <form className="category-create" onSubmit={(event) => void createCategory(event)}>
        <label>
          Names
          <textarea
            value={name}
            rows={2}
            placeholder="Groceries, Utilities, Rent"
            required
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        <label>
          Category group
          <select value={scope} onChange={(event) => setScope(event.target.value as Scope)}>
            <option value="BUDGET">Budget allocation</option>
            <option value="OTHER_INFLOW">Other Inflows</option>
          </select>
        </label>
        {scope === 'BUDGET' && <label>
          Budget bucket
          <select value={bucket} onChange={(event) => setBucket(event.target.value as Bucket)}>
            {buckets.map((value) => <option key={value} value={value}>{labels[value]}</option>)}
          </select>
        </label>}
        <button className="primary-button" type="submit">Create subcategories</button>
      </form>

      {[
        ...buckets.map((value) => ({
          key: value,
          title: labels[value],
          categories: visible.filter((category) => (
            category.scope === 'BUDGET' && category.budget_bucket === value
          )),
        })),
        {
          key: 'OTHER_INFLOW',
          title: 'Other Inflows',
          categories: visible.filter((category) => category.scope === 'OTHER_INFLOW'),
        },
      ].map(({ key, title, categories: group }) => {
        if (!group.length) return null
        return (
          <section className="category-group" key={key} aria-labelledby={`bucket-${key}`}>
            <h3 id={`bucket-${key}`}>{title}</h3>
            <div className="category-list">
              {group.map((category) => {
                const draft = drafts[category.id] ?? { name: category.name, budget_bucket: category.budget_bucket }
                return (
                  <div className="category-row" key={category.id}>
                    <label>
                      Subcategory name
                      <input
                        value={draft.name}
                        maxLength={100}
                        onChange={(event) => setDrafts((current) => ({
                          ...current,
                          [category.id]: { ...draft, name: event.target.value },
                        }))}
                      />
                    </label>
                    {category.scope === 'BUDGET' && <label>
                      Bucket
                      <select
                        value={draft.budget_bucket || 'NEEDS'}
                        onChange={(event) => setDrafts((current) => ({
                          ...current,
                          [category.id]: { ...draft, budget_bucket: event.target.value as Bucket },
                        }))}
                      >
                        {buckets.map((option) => <option key={option} value={option}>{labels[option]}</option>)}
                      </select>
                    </label>}
                    <span className={`category-state ${category.is_active ? '' : 'category-state--archived'}`}>
                      {category.is_active ? 'Active' : 'Archived'}
                    </span>
                    <button
                      type="button"
                      onClick={() => void save(
                        `/api/subcategories/${category.id}/`,
                        'PATCH',
                        draft,
                        'Subcategory updated.',
                      )}
                    >
                      Save
                    </button>
                    <button
                      className="quiet-button"
                      type="button"
                      onClick={() => void save(
                        `/api/subcategories/${category.id}/`,
                        'PATCH',
                        { is_active: !category.is_active },
                        category.is_active ? 'Subcategory archived.' : 'Subcategory reactivated.',
                      )}
                    >
                      {category.is_active ? 'Archive' : 'Reactivate'}
                    </button>
                  </div>
                )
              })}
            </div>
          </section>
        )
      })}
      {visible.length === 0 && <p className="message">No subcategories yet.</p>}
    </section>
  )
}

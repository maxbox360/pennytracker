import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { apiFetch } from './api'

type Bucket = 'NEEDS' | 'WANTS' | 'SAVINGS'
type Subcategory = { id: number; name: string; budget_bucket: Bucket; is_active: boolean }
type Draft = { name: string; budget_bucket: Bucket }

const buckets: Bucket[] = ['NEEDS', 'WANTS', 'SAVINGS']
const labels: Record<Bucket, string> = { NEEDS: 'Needs', WANTS: 'Wants', SAVINGS: 'Savings' }

function errorText(value: unknown): string {
  if (typeof value === 'string') return value
  if (value && typeof value === 'object') return JSON.stringify(value)
  return 'The request failed.'
}

export default function Categories() {
  const [categories, setCategories] = useState<Subcategory[]>([])
  const [drafts, setDrafts] = useState<Record<number, Draft>>({})
  const [name, setName] = useState('')
  const [bucket, setBucket] = useState<Bucket>('NEEDS')
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
    if (await save('/api/subcategories/', 'POST', { name, budget_bucket: bucket }, 'Subcategory created.')) {
      setName('')
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
          Name
          <input value={name} maxLength={100} required onChange={(event) => setName(event.target.value)} />
        </label>
        <label>
          Budget bucket
          <select value={bucket} onChange={(event) => setBucket(event.target.value as Bucket)}>
            {buckets.map((value) => <option key={value} value={value}>{labels[value]}</option>)}
          </select>
        </label>
        <button className="primary-button" type="submit">Create subcategory</button>
      </form>

      {buckets.map((value) => {
        const group = visible.filter((category) => category.budget_bucket === value)
        if (!group.length) return null
        return (
          <section className="category-group" key={value} aria-labelledby={`bucket-${value}`}>
            <h3 id={`bucket-${value}`}>{labels[value]}</h3>
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
                    <label>
                      Bucket
                      <select
                        value={draft.budget_bucket}
                        onChange={(event) => setDrafts((current) => ({
                          ...current,
                          [category.id]: { ...draft, budget_bucket: event.target.value as Bucket },
                        }))}
                      >
                        {buckets.map((option) => <option key={option} value={option}>{labels[option]}</option>)}
                      </select>
                    </label>
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

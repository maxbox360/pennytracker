import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { apiFetch } from './api'

type AccountType = 'CHECKING' | 'SAVINGS' | 'CREDIT_CARD' | 'LOAN' | 'INVESTMENT' | 'OTHER'
type Account = {
  id: number
  name: string
  institution: string
  account_type: AccountType
  is_savings_destination: boolean
  is_active: boolean
}
type Draft = Pick<Account, 'name' | 'institution' | 'account_type' | 'is_savings_destination'>

const types: Record<AccountType, string> = {
  CHECKING: 'Checking',
  SAVINGS: 'Savings',
  CREDIT_CARD: 'Credit Card',
  LOAN: 'Loan',
  INVESTMENT: 'Investment',
  OTHER: 'Other',
}
const emptyDraft: Draft = { name: '', institution: '', account_type: 'CHECKING', is_savings_destination: false }

function errorText(value: unknown, status: number): string {
  if (value && typeof value === 'object') {
    for (const [field, messages] of Object.entries(value)) {
      const message = Array.isArray(messages) ? messages[0] : messages
      if (typeof message === 'string') {
        return field === 'detail' || field === 'non_field_errors'
          ? message
          : `${field.replace(/_/g, ' ')}: ${message}`
      }
    }
  }
  return `Unable to save account (${status}). Please try again.`
}

function AccountForm({ initial, saving, onSave, onCancel }: {
  initial: Draft
  saving: boolean
  onSave: (draft: Draft) => Promise<boolean>
  onCancel?: () => void
}) {
  const [draft, setDraft] = useState(initial)
  const eligible = draft.account_type === 'SAVINGS' || draft.account_type === 'INVESTMENT'
  async function submit(event: FormEvent) {
    event.preventDefault()
    if (await onSave(draft) && !onCancel) setDraft(emptyDraft)
  }
  return (
    <form className="account-form" onSubmit={(event) => void submit(event)}>
      <label>Name<input required maxLength={100} value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} /></label>
      <label>Institution <span>(optional)</span><input maxLength={100} value={draft.institution} placeholder="Example Bank" onChange={(event) => setDraft({ ...draft, institution: event.target.value })} /></label>
      <label>Type<select value={draft.account_type} onChange={(event) => setDraft({ ...draft, account_type: event.target.value as AccountType })}>
        {Object.entries(types).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select></label>
      {(eligible || draft.is_savings_destination) && <label className="account-destination">
        <input type="checkbox" checked={draft.is_savings_destination} onChange={(event) => setDraft({ ...draft, is_savings_destination: event.target.checked })} />
        Savings Destination
      </label>}
      {!eligible && draft.is_savings_destination && <p className="account-warning">Clear Savings Destination or choose Savings or Investment when changing this setting or type.</p>}
      <div className="account-form-actions">
        <button className="primary-button" disabled={saving} type="submit">{onCancel ? 'Save changes' : 'Add account'}</button>
        {onCancel && <button className="secondary-button" disabled={saving} type="button" onClick={onCancel}>Cancel</button>}
      </div>
    </form>
  )
}

export default function Accounts() {
  const [accounts, setAccounts] = useState<Account[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [editing, setEditing] = useState<number | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  async function load() {
    const response = await apiFetch('/api/accounts/')
    if (!response.ok) throw new Error(`Unable to load accounts (${response.status}).`)
    setAccounts(await response.json() as Account[])
  }
  useEffect(() => {
    load().catch((err: unknown) => setError(err instanceof Error ? err.message : 'Unable to load accounts.'))
      .finally(() => setLoading(false))
  }, [])

  async function save(path: string, method: 'POST' | 'PATCH', body: object, message: string) {
    setSaving(true)
    setError('')
    setNotice('')
    try {
      const response = await apiFetch(path, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      const result: unknown = await response.json()
      if (!response.ok) throw new Error(errorText(result, response.status))
      await load()
      setEditing(null)
      setNotice(message)
      return true
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to save account.')
      return false
    } finally {
      setSaving(false)
    }
  }

  function update(account: Account, draft: Draft) {
    const changes: Partial<Draft> = {}
    if (draft.name !== account.name) changes.name = draft.name
    if (draft.institution !== account.institution) changes.institution = draft.institution
    if (draft.account_type !== account.account_type) changes.account_type = draft.account_type
    if (draft.is_savings_destination !== account.is_savings_destination) changes.is_savings_destination = draft.is_savings_destination
    return save(`/api/accounts/${account.id}/`, 'PATCH', changes, 'Account updated.')
  }

  return (
    <section className="accounts-panel" aria-label="Manage accounts">
      {error && <p className="message message--error" role="alert">{error}</p>}
      {notice && <p className="table-notice" role="status">{notice}</p>}
      {!loading && !error && accounts.length === 0 && <p>Create an account before importing transaction CSVs.</p>}
      <h2>Add account</h2>
      <AccountForm initial={emptyDraft} saving={saving} onSave={(draft) => save('/api/accounts/', 'POST', draft, 'Account created.')} />
      <p className="account-help">Savings Destination is metadata only; transfers count as Savings only when explicitly marked on a transaction.</p>
      {loading && <p>Loading accounts…</p>}
      {[true, false].map((active) => {
        const group = accounts.filter((account) => account.is_active === active)
        return <section className="account-group" key={String(active)}>
          <h2>{active ? 'Active Accounts' : 'Archived Accounts'}</h2>
          {group.length === 0 ? <p>No {active ? 'active' : 'archived'} accounts.</p> : <div className="table-scroll">
            <table>
              <thead><tr><th>Name</th><th>Institution</th><th>Type</th><th>Savings Destination</th><th>Status</th><th>Actions</th></tr></thead>
              <tbody>{group.map((account) => <tr key={account.id}>
                <td>{account.name}</td><td>{account.institution || '—'}</td><td>{types[account.account_type]}</td>
                <td>{account.is_savings_destination ? 'Yes' : 'No'}</td><td>{active ? 'Active' : 'Archived'}</td>
                <td className="account-actions">
                  <button type="button" disabled={saving} onClick={() => setEditing(editing === account.id ? null : account.id)}>Edit</button>
                  <button type="button" disabled={saving} onClick={() => {
                    if (active && !window.confirm(`Archive ${account.name}? Historical transactions will remain available.`)) return
                    void save(`/api/accounts/${account.id}/`, 'PATCH', { is_active: !active }, active ? 'Account archived.' : 'Account reactivated.')
                  }}>{active ? 'Archive' : 'Reactivate'}</button>
                </td>
              </tr>)}</tbody>
            </table>
          </div>}
          {group.map((account) => editing === account.id && <div className="account-edit" key={account.id}>
            <h3>Edit {account.name}</h3>
            <AccountForm key={account.id} initial={account} saving={saving} onSave={(draft) => update(account, draft)} onCancel={() => setEditing(null)} />
          </div>)}
        </section>
      })}
    </section>
  )
}

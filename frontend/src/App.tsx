import { useEffect, useState } from 'react'
import { apiFetch } from './api'
import TransactionTable from './TransactionTable'
import Categories from './Categories'
import Rules from './Rules'
import Reports from './Reports'
import Accounts from './Accounts'
import Debts from './Debts'
import Recurring from './Recurring'
import './App.css'

type Account = { id: number; name: string; is_active: boolean }
type Classification = 'UNKNOWN' | 'EXPENSE' | 'INCOME' | 'REFUND' | 'TRANSFER' | 'EXCLUDED' | 'OTHER_INFLOW'
type Preview = {
  account: { id: number; name: string }
  filename: string
  source_row_count: number
  valid_row_count: number
  invalid_row_count: number
  new_row_count: number
  already_represented_row_count: number
  possible_duplicate_row_count: number
  review_required_row_count: number
  valid_rows: {
    source_row: number
    date: string
    posted_date: string | null
    raw_description: string
    amount: string
    source_type: string
    classification: Classification
    overlap_status: 'NEW' | 'ALREADY_REPRESENTED' | 'POSSIBLE_DUPLICATE'
    overlap_reason: string
    categorization: {
      status: 'AUTO_RESOLVED' | 'RULE_CONFLICT' | 'NOT_CATEGORIZED'
      classification: Classification | null
      savings_effect: 'NONE' | 'CONTRIBUTION' | 'WITHDRAWAL'
      subcategory: { id: number; name: string; budget_bucket: 'NEEDS' | 'WANTS' | 'SAVINGS' | null } | null
      rules: {
        id: number
        classification: Classification
        match_type: 'CONTAINS' | 'STARTS_WITH'
        match_value: string
        savings_effect: 'NONE' | 'CONTRIBUTION' | 'WITHDRAWAL'
        subcategory_name: string | null
      }[]
    }
    duplicate_candidates: {
      id: number
      date: string
      raw_description: string
      merchant: string
      amount: string
      source_type: string
      account: string
    }[]
  }[]
  invalid_rows: { source_row: number; errors: string[] }[]
  already_imported: boolean
  can_import: boolean
}
type ImportResult = {
  filename: string
  account: string
  source_row_count: number
  imported_row_count: number
  already_represented_row_count: number
  possible_duplicate_row_count: number
}
type Theme = 'light' | 'dark' | 'system'

const classificationLabels: Record<Classification, string> = {
  UNKNOWN: 'Unknown',
  EXPENSE: 'Expense',
  INCOME: 'Income',
  REFUND: 'Refund',
  TRANSFER: 'Transfer',
  EXCLUDED: 'Excluded',
  OTHER_INFLOW: 'Other inflow',
}

function formatAmount(amount: string) {
  const negative = amount.startsWith('-')
  const [whole, fraction = '00'] = (negative ? amount.slice(1) : amount).split('.')
  const groupedWhole = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  return `${negative ? '-' : ''}$${groupedWhole}.${fraction.padEnd(2, '0')}`
}

function apiErrorMessage(result: unknown, status: number): string {
  const data = (result ?? {}) as Record<string, unknown>
  if (typeof data.detail === 'string') return data.detail
  const first = Object.entries(data).find(([, value]) => Array.isArray(value) && typeof value[0] === 'string')
  if (first) {
    const [field, messages] = first as [string, string[]]
    return field === 'non_field_errors' ? messages[0] : `${field[0].toUpperCase()}${field.slice(1).replace(/_/g, ' ')}: ${messages[0]}`
  }
  return `The import request failed (${status}). Please try again.`
}

type PreviewOutcome = {
  classification: Classification | null
  savings_effect: 'NONE' | 'CONTRIBUTION' | 'WITHDRAWAL'
  subcategory?: { name: string; budget_bucket: 'NEEDS' | 'WANTS' | 'SAVINGS' | null } | null
  subcategory_name?: string | null
}

function ruleText(rule: { id: number; match_type: string; match_value: string }) {
  return `#${rule.id} ${rule.match_type === 'STARTS_WITH' ? 'starts with' : 'contains'} “${rule.match_value}”`
}

function outcomeText(outcome: PreviewOutcome) {
  const name = outcome.subcategory?.name ?? outcome.subcategory_name
  const bucket = outcome.subcategory?.budget_bucket
  return [
    outcome.classification ? classificationLabels[outcome.classification] : 'Unspecified',
    outcome.savings_effect !== 'NONE' ? `Savings ${outcome.savings_effect.toLowerCase()}` : '',
    name ? `${name}${bucket ? ` (${bucket.charAt(0)}${bucket.slice(1).toLowerCase()})` : ''}` : '',
  ].filter(Boolean).join(' · ')
}

function PageHeroGraphic({ view, previewing }: { view: 'transactions' | 'review' | 'categories' | 'rules' | 'reports' | 'accounts' | 'debts' | 'recurring'; previewing: boolean }) {
  if (previewing) {
    return (
      <svg className="page-hero-graphic" aria-hidden="true" width="112" height="56" viewBox="0 0 112 56" fill="none">
        <rect x="8" y="6" width="56" height="44" rx="7" fill="var(--surface-alt)" stroke="var(--border-strong)" strokeWidth="1.25" />
        <path d="M18 19h24M18 28h36M18 37h20" stroke="var(--text-secondary)" strokeWidth="1.5" strokeLinecap="round" />
        <circle cx="80" cy="30" r="15" fill="var(--surface-alt)" stroke="var(--positive)" strokeWidth="1.5" />
        <path d="M74 30.5l4 4 8.5-9" stroke="var(--positive)" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    )
  }

  switch (view) {
    case 'review':
      return (
        <svg className="page-hero-graphic" aria-hidden="true" width="112" height="56" viewBox="0 0 112 56" fill="none">
          <rect x="8" y="10" width="54" height="36" rx="7" fill="var(--surface-alt)" stroke="var(--border-strong)" strokeWidth="1.25" />
          <circle cx="19" cy="22" r="3" fill="var(--text-secondary)" />
          <path d="M27 22h24M17 33h32" stroke="var(--muted)" strokeWidth="1.5" strokeLinecap="round" />
          <path d="M78 8l18 6.5v12c0 11.5-7.8 19-18 22-10.2-3-18-10.5-18-22v-12L78 8z" fill="var(--surface-alt)" stroke="var(--border-strong)" strokeWidth="1.25" />
          <path d="M71.5 28l4.5 4.5 9.5-9.5" stroke="var(--text)" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      )
    case 'categories':
      return (
        <svg className="page-hero-graphic" aria-hidden="true" width="112" height="56" viewBox="0 0 112 56" fill="none">
          <rect x="8" y="8" width="42" height="18" rx="5" fill="var(--surface-alt)" stroke="var(--border-strong)" strokeWidth="1.25" />
          <circle cx="17" cy="17" r="3" fill="var(--chart-needs)" />
          <path d="M25 17h16" stroke="var(--text-secondary)" strokeWidth="1.5" strokeLinecap="round" />
          <rect x="56" y="8" width="46" height="18" rx="5" fill="var(--surface-alt)" stroke="var(--border-strong)" strokeWidth="1.25" />
          <circle cx="65" cy="17" r="3" fill="var(--chart-wants)" />
          <path d="M73 17h20" stroke="var(--text-secondary)" strokeWidth="1.5" strokeLinecap="round" />
          <rect x="22" y="32" width="66" height="18" rx="5" fill="var(--surface-alt)" stroke="var(--border-strong)" strokeWidth="1.25" />
          <circle cx="32" cy="41" r="3" fill="var(--chart-savings)" />
          <path d="M41 41h34" stroke="var(--text-secondary)" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
      )
    case 'rules':
      return (
        <svg className="page-hero-graphic" aria-hidden="true" width="112" height="56" viewBox="0 0 112 56" fill="none">
          <rect x="8" y="10" width="34" height="36" rx="6" fill="var(--surface-alt)" stroke="var(--border-strong)" strokeWidth="1.25" />
          <path d="M15 21h20M15 28h14M15 35h18" stroke="var(--muted)" strokeWidth="1.5" strokeLinecap="round" />
          <path d="M46 28h16" stroke="var(--border-strong)" strokeWidth="1.5" strokeLinecap="round" strokeDasharray="3 3" />
          <path d="M58 24l5 4-5 4" stroke="var(--text-secondary)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          <rect x="68" y="10" width="36" height="36" rx="7" fill="var(--surface-alt)" stroke="var(--border-strong)" strokeWidth="1.25" />
          <circle cx="86" cy="28" r="9" stroke="var(--text)" strokeWidth="1.5" />
          <path d="M82.5 28l2.5 2.5 5-5.5" stroke="var(--text)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      )
    case 'reports':
      return (
        <svg className="page-hero-graphic" aria-hidden="true" width="112" height="56" viewBox="0 0 112 56" fill="none">
          <circle cx="26" cy="28" r="16" stroke="var(--border)" strokeWidth="4" />
          <circle cx="26" cy="28" r="16" stroke="var(--chart-needs)" strokeWidth="4" strokeDasharray="44 80" strokeLinecap="round" />
          <circle cx="26" cy="28" r="16" stroke="var(--chart-savings)" strokeWidth="4" strokeDashoffset="-52" strokeDasharray="28 80" strokeLinecap="round" />
          <path d="M54 39c7-3 11-13 18-12s10 5 16-3 8-12 12-14" stroke="var(--text)" strokeWidth="1.75" strokeLinecap="round" />
          <circle cx="100" cy="10" r="2.75" fill="var(--text)" />
          <path d="M54 45h48" stroke="var(--border)" strokeWidth="1.25" strokeLinecap="round" />
        </svg>
      )
    case 'recurring':
      return (
        <svg className="page-hero-graphic" aria-hidden="true" width="112" height="56" viewBox="0 0 112 56" fill="none">
          <rect x="10" y="9" width="46" height="38" rx="6" fill="var(--surface-alt)" stroke="var(--border-strong)" strokeWidth="1.25" />
          <path d="M10 19h46" stroke="var(--border-strong)" strokeWidth="1.25" />
          <circle cx="21" cy="28" r="2.5" fill="var(--text-secondary)" />
          <circle cx="33" cy="28" r="2.5" fill="var(--muted)" />
          <circle cx="45" cy="28" r="2.5" fill="var(--chart-needs)" />
          <circle cx="21" cy="38" r="2.5" fill="var(--muted)" />
          <circle cx="33" cy="38" r="2.5" fill="var(--text-secondary)" />
          <circle cx="82" cy="28" r="16" fill="var(--surface-alt)" stroke="var(--border)" strokeWidth="1.25" />
          <path d="M91 25a9.5 9.5 0 0 0-16.5-4M73 31a9.5 9.5 0 0 0 16.5 4" stroke="var(--text-secondary)" strokeWidth="1.6" strokeLinecap="round" />
        </svg>
      )
    case 'debts':
      return (
        <svg className="page-hero-graphic" aria-hidden="true" width="112" height="56" viewBox="0 0 112 56" fill="none">
          <path d="M10 16c10 2 18 9 26 14s16 8 26 12" stroke="var(--text-secondary)" strokeWidth="1.75" strokeLinecap="round" />
          <path d="M10 44h52" stroke="var(--border)" strokeWidth="1.25" strokeLinecap="round" />
          <circle cx="84" cy="28" r="15" fill="var(--surface-alt)" stroke="var(--border)" strokeWidth="3" />
          <circle cx="84" cy="28" r="15" stroke="var(--chart-savings)" strokeWidth="3" strokeDasharray="64 95" strokeLinecap="round" />
          <path d="M79.5 28.5l3 3 6.5-6.5" stroke="var(--text)" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      )
    case 'accounts':
      return (
        <svg className="page-hero-graphic" aria-hidden="true" width="112" height="56" viewBox="0 0 112 56" fill="none">
          <rect x="8" y="12" width="56" height="34" rx="6" fill="var(--surface-alt)" stroke="var(--border-strong)" strokeWidth="1.25" />
          <rect x="8" y="19" width="56" height="5" fill="var(--border)" />
          <rect x="15" y="30" width="11" height="8" rx="2" fill="var(--border-strong)" />
          <path d="M33 35h20" stroke="var(--muted)" strokeWidth="1.5" strokeLinecap="round" />
          <path d="M84 10l16 5.5v11.5c0 10-7 17-16 20-9-3-16-10-16-20V15.5L84 10z" fill="var(--surface-alt)" stroke="var(--border-strong)" strokeWidth="1.25" />
          <circle cx="84" cy="27" r="4.5" stroke="var(--text-secondary)" strokeWidth="1.5" />
        </svg>
      )
    case 'transactions':
    default:
      return (
        <svg className="page-hero-graphic" aria-hidden="true" width="112" height="56" viewBox="0 0 112 56" fill="none">
          <rect x="8" y="14" width="54" height="32" rx="6" fill="var(--surface-alt)" stroke="var(--border-strong)" strokeWidth="1.25" />
          <rect x="15" y="21" width="10" height="7" rx="1.5" fill="var(--border-strong)" />
          <path d="M15 36h22M43 36h10" stroke="var(--muted)" strokeWidth="1.5" strokeLinecap="round" />
          <rect x="46" y="8" width="56" height="32" rx="6" fill="var(--surface)" stroke="var(--border-strong)" strokeWidth="1.25" />
          <path d="M55 29c6-7 11-3 17-10s10-2 16-8" stroke="var(--text)" strokeWidth="1.75" strokeLinecap="round" />
          <circle cx="88" cy="11" r="2.5" fill="var(--text)" />
          <path d="M55 34h32" stroke="var(--border)" strokeWidth="1.25" strokeLinecap="round" />
        </svg>
      )
  }
}

function App() {
  const [reload, setReload] = useState(0)
  const [reviewReason, setReviewReason] = useState('')
  const [reviewDates, setReviewDates] = useState({ start: '', end: '' })
  const [reportsVisited, setReportsVisited] = useState(false)
  const [view, setView] = useState<'transactions' | 'review' | 'categories' | 'rules' | 'reports' | 'accounts' | 'debts' | 'recurring'>('transactions')
  const [theme, setTheme] = useState<Theme>(() => {
    const saved = localStorage.getItem('pennytracker-theme')
    return saved === 'light' || saved === 'dark' || saved === 'system' ? saved : 'system'
  })
  const [showImport, setShowImport] = useState(false)
  const [accounts, setAccounts] = useState<Account[]>([])
  const [accountId, setAccountId] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [importResult, setImportResult] = useState<ImportResult | null>(null)
  const [importError, setImportError] = useState('')
  const [importing, setImporting] = useState(false)

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    localStorage.setItem('pennytracker-theme', theme)
  }, [theme])

  useEffect(() => {
    if (!showImport) return
    apiFetch('/api/accounts/')
      .then(async (response) => {
        if (!response.ok) throw new Error(`Request failed (${response.status})`)
        const result: Account[] = await response.json()
        setAccounts(result.filter((account) => account.is_active))
      })
      .catch((loadError) => {
        setImportError(loadError instanceof Error ? loadError.message : 'Unable to load accounts')
      })
  }, [showImport])

  function closeImport() {
    setShowImport(false)
    setPreview(null)
    setFile(null)
    setAccountId('')
    setImportError('')
  }

  async function submitImport(action: 'preview' | 'confirm') {
    if (!file || !accountId) return
    setImportError('')
    setImportResult(null)
    setImporting(true)
    const formData = new FormData()
    formData.append('account', accountId)
    formData.append('file', file)
    try {
      const response = await apiFetch(`/api/imports/${action === 'preview' ? 'preview/' : ''}`, {
        method: 'POST',
        body: formData,
      })
      const result = await response.json()
      if (!response.ok) throw new Error(apiErrorMessage(result, response.status))
      if (action === 'preview') {
        setPreview(result as Preview)
      } else {
        setImportResult(result as ImportResult)
        closeImport()
        setReload((current) => current + 1)
      }
    } catch (requestError) {
      setImportError(requestError instanceof Error ? requestError.message : 'Import request failed')
    } finally {
      setImporting(false)
    }
  }

  const previewHint = importing
    ? 'Preview is being generated.'
    : !accountId && !file
      ? 'Select an account and choose a CSV file to continue.'
      : !accountId
        ? 'Select an account to preview this import.'
        : !file
          ? 'Choose a CSV file to preview this import.'
          : ''
  const confirmHint = !preview || importing || preview.can_import
    ? ''
    : preview.already_imported
      ? 'This exact file was already imported to this account, so it cannot be imported again.'
      : 'Confirm is unavailable because the file has invalid rows. Fix the CSV and preview it again.'

  const previewing = view === 'transactions' && preview !== null

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a className="brand" href="/" aria-label="PennyTracker home">
          <span className="brand-mark" aria-hidden="true">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
              <circle cx="12" cy="12" r="9.5" fill="var(--accent)" opacity="0.16" />
              <circle cx="12" cy="12" r="7.5" stroke="currentColor" strokeWidth="1.75" />
              <path d="M12 7v10M14.75 9.25c0-1.1-1.23-1.75-2.75-1.75s-2.75.65-2.75 1.75 1.1 1.65 2.75 2.15 2.75 1.05 2.75 2.25-1.23 1.85-2.75 1.85-2.75-.75-2.75-1.85" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" />
              <path d="M18.5 4.5l.7 1.6 1.6.7-1.6.7-.7 1.6-.7-1.6-1.6-.7 1.6-.7.7-1.6z" fill="currentColor" />
            </svg>
          </span>
          <span>PennyTracker</span>
        </a>
        <nav aria-label="Main navigation">
          <button
            className={`nav-item ${view === 'transactions' ? 'nav-item--active' : ''}`}
            type="button"
            aria-current={view === 'transactions' ? 'page' : undefined}
            onClick={() => setView('transactions')}
          >
            <svg className="nav-icon" aria-hidden="true" width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M2.5 4h11M2.5 8h11M2.5 12h7" />
            </svg>
            Transactions
          </button>
          <button
            className={`nav-item ${view === 'review' ? 'nav-item--active' : ''}`}
            type="button"
            aria-current={view === 'review' ? 'page' : undefined}
            onClick={() => { setReviewReason(''); setReviewDates({ start: '', end: '' }); setView('review') }}
          >
            <svg className="nav-icon" aria-hidden="true" width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="8" cy="8" r="5.5" />
              <path d="M5.75 8.25 7.25 9.75 10.5 6.5" />
            </svg>
            Review
          </button>
          <button
            className={`nav-item ${view === 'categories' ? 'nav-item--active' : ''}`}
            type="button"
            aria-current={view === 'categories' ? 'page' : undefined}
            onClick={() => setView('categories')}
          >
            <svg className="nav-icon" aria-hidden="true" width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <rect x="2.5" y="2.5" width="4.5" height="4.5" rx="1" />
              <rect x="9" y="2.5" width="4.5" height="4.5" rx="1" />
              <rect x="2.5" y="9" width="4.5" height="4.5" rx="1" />
              <rect x="9" y="9" width="4.5" height="4.5" rx="1" />
            </svg>
            Categories
          </button>
          <button
            className={`nav-item ${view === 'rules' ? 'nav-item--active' : ''}`}
            type="button"
            aria-current={view === 'rules' ? 'page' : undefined}
            onClick={() => setView('rules')}
          >
            <svg className="nav-icon" aria-hidden="true" width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M2.5 4.5h11M4.5 8h7M6.5 11.5h3" />
            </svg>
            Rules
          </button>
          <button
            className={`nav-item ${view === 'reports' ? 'nav-item--active' : ''}`}
            type="button"
            aria-current={view === 'reports' ? 'page' : undefined}
            onClick={() => {
              if (view !== 'reports') setReload((current) => current + 1)
              setReportsVisited(true)
              setView('reports')
            }}
          >
            <svg className="nav-icon" aria-hidden="true" width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M3 13V8M8 13V3M13 13V6" />
            </svg>
            Reports
          </button>
          <button
            className={`nav-item ${view === 'recurring' ? 'nav-item--active' : ''}`}
            type="button"
            aria-current={view === 'recurring' ? 'page' : undefined}
            onClick={() => setView('recurring')}
          >
            <svg className="nav-icon" aria-hidden="true" width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12.5 5.5A5 5 0 0 0 3.5 6M3.5 10.5A5 5 0 0 0 12.5 10" />
              <path d="M12.5 2.5v3h-3M3.5 13.5v-3h3" />
            </svg>
            Recurring
          </button>
          <button
            className={`nav-item ${view === 'debts' ? 'nav-item--active' : ''}`}
            type="button"
            aria-current={view === 'debts' ? 'page' : undefined}
            onClick={() => setView('debts')}
          >
            <svg className="nav-icon" aria-hidden="true" width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <rect x="2" y="3.5" width="12" height="9" rx="1.5" />
              <path d="M2 6.5h12M5 10h2.5" />
            </svg>
            Debts
          </button>
          <button
            className={`nav-item ${view === 'accounts' ? 'nav-item--active' : ''}`}
            type="button"
            aria-current={view === 'accounts' ? 'page' : undefined}
            onClick={() => setView('accounts')}
          >
            <svg className="nav-icon" aria-hidden="true" width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <ellipse cx="8" cy="4" rx="5" ry="2" />
              <path d="M3 4v4c0 1.1 2.24 2 5 2s5-.9 5-2V4" />
              <path d="M3 8v4c0 1.1 2.24 2 5 2s5-.9 5-2V8" />
            </svg>
            Accounts &amp; Backups
          </button>
        </nav>
        <div className="sidebar-footer">
          <label className="theme-control">
            Theme
            <select aria-label="Theme" value={theme} onChange={(event) => setTheme(event.target.value as Theme)}>
              <option value="light">Light</option>
              <option value="dark">Dark</option>
              <option value="system">System</option>
            </select>
          </label>
          <p className="local-note">Your finances stay on this device.</p>
        </div>
      </aside>

      <main className="main-content">
        <header className="page-header page-hero">
          <div className="page-hero-content">
            <span className="page-hero-badge" aria-hidden="true">
              {previewing
                ? 'CSV Import Verification'
                : view === 'review'
                  ? 'Smart Triage & Hygiene'
                  : view === 'categories'
                    ? '50/30/20 Envelope Architecture'
                    : view === 'rules'
                      ? 'Automation Engine'
                      : view === 'reports'
                        ? 'Financial Intelligence'
                        : view === 'recurring'
                          ? 'Bills & Subscriptions'
                          : view === 'debts'
                            ? 'Paydown Velocity'
                            : view === 'accounts'
                              ? 'Local Vault & Ledger'
                              : 'Unified Cashflow Feed'}
            </span>
            <h1>
              {previewing
                ? 'Import preview'
                : view === 'review'
                  ? 'Review'
                  : view === 'categories'
                    ? 'Categories'
                    : view === 'rules'
                      ? 'Rules'
                      : view === 'reports'
                        ? 'Reports'
                        : view === 'recurring'
                          ? 'Recurring'
                          : view === 'debts'
                            ? 'Debts'
                            : view === 'accounts'
                              ? 'Accounts & Backups'
                              : 'Transactions'}
            </h1>
            <p className="page-description">
              {previewing
                ? 'Review the rows below, then confirm or close without importing.'
                : view === 'review'
                  ? 'Resolve unknown classifications, uncategorized activity, and possible duplicates.'
                  : view === 'categories'
                    ? 'Manage the subcategories used to organize transactions.'
                    : view === 'rules'
                      ? 'Manage the deterministic rules used to classify and categorize transactions.'
                      : view === 'reports'
                        ? 'Explore All Time, yearly, and monthly accounting with trends and auditable transaction details.'
                        : view === 'recurring'
                          ? 'Manage monthly bills, monthly subscriptions, and annual renewals with effective monthly and annual costs.'
                          : view === 'debts'
                            ? 'Track credit cards, loans, student loan servicers, and historical balance paydown snapshots.'
                            : view === 'accounts'
                              ? 'Manage accounts in the UI, inspect CSV import batches, and download SQLite database backups.'
                              : 'A clear view of activity across your accounts.'}
            </p>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '16px', flexShrink: 0 }}>
            <PageHeroGraphic view={view} previewing={previewing} />
            {view !== 'categories' && view !== 'rules' && (
              <div className="page-actions">
                {!previewing && (
                  <button className="secondary-button" type="button" onClick={() => setReload((current) => current + 1)}>
                    Refresh
                  </button>
                )}
                {previewing && (
                  <button
                    className="primary-button"
                    type="button"
                    disabled={!preview.can_import || importing}
                    onClick={() => void submitImport('confirm')}
                  >
                    {importing ? 'Importing…' : 'Confirm import'}
                  </button>
                )}
                {view === 'transactions' && (
                  <button
                    className={previewing ? 'secondary-button' : 'primary-button'}
                    type="button"
                    disabled={previewing && importing}
                    onClick={() => {
                      if (showImport) {
                        closeImport()
                      } else {
                        setShowImport(true)
                        setImportResult(null)
                        setImportError('')
                      }
                    }}
                  >
                    {previewing ? 'Close import' : showImport ? 'Cancel import' : 'Import CSV'}
                  </button>
                )}
              </div>
            )}
          </div>
        </header>

        {importResult && !showImport && view === 'transactions' && (
          <p className="import-success" role="status">
            Imported {importResult.imported_row_count} new or possible-duplicate rows; skipped
            {' '}{importResult.already_represented_row_count} already represented rows from
            {' '}{importResult.filename} into {importResult.account}.
            {' '}<button type="button" className="link-button" onClick={() => setImportResult(null)}>Dismiss</button>
          </p>
        )}

        {showImport && view === 'transactions' && (
          <section className="import-card" aria-labelledby="import-heading">
            <div className="import-heading" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '16px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '14px' }}>
                <span className="kpi-icon-badge" aria-hidden="true">
                  <svg width="22" height="22" viewBox="0 0 24 24" fill="none">
                    <path d="M14 2H7a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7l-5-5z" fill="var(--accent-soft)" stroke="var(--accent)" strokeWidth="1.75" strokeLinejoin="round" />
                    <path d="M14 2v5h5M12 17v-6m0 0-2.5 2.5M12 11l2.5 2.5" stroke="var(--accent)" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" />
                    <circle cx="19" cy="4" r="1.5" fill="var(--chart-wants)" />
                  </svg>
                </span>
                <div>
                  <h2 id="import-heading">Import transactions</h2>
                  <p>Select the destination account; the filename is kept for reference only.</p>
                </div>
              </div>
              <svg aria-hidden="true" width="88" height="44" viewBox="0 0 88 44" fill="none" style={{ flexShrink: 0 }}>
                <rect x="4" y="6" width="52" height="32" rx="7" fill="var(--surface-alt)" stroke="var(--border-strong)" strokeDasharray="3 3" strokeWidth="1.4" />
                <path d="M16 18h20M16 25h28" stroke="var(--muted)" strokeWidth="1.8" strokeLinecap="round" />
                <circle cx="64" cy="22" r="14" fill="var(--accent-soft)" stroke="var(--accent)" strokeWidth="1.5" />
                <path d="M64 27V17m0 0-3.5 3.5M64 17l3.5 3.5" stroke="var(--accent)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                <path d="M80 9l1 2.2 2.2 1-2.2 1-1 2.2-1-2.2-2.2-1 2.2-1 1-2.2z" fill="var(--chart-wants)" />
              </svg>
            </div>
            {!preview && (
            <div className="import-controls">
              <label>
                Account
                <select disabled={importing} value={accountId} onChange={(event) => {
                  setAccountId(event.target.value)
                  setPreview(null)
                  setImportResult(null)
                }}>
                  <option value="">Select an account</option>
                  {accounts.map((account) => (
                    <option key={account.id} value={account.id}>{account.name}</option>
                  ))}
                </select>
              </label>
              <label>
                CSV file (20 MiB maximum)
                <input
                  disabled={importing}
                  type="file"
                  accept=".csv,text/csv"
                  onChange={(event) => {
                    setFile(event.target.files?.[0] ?? null)
                    setPreview(null)
                    setImportResult(null)
                  }}
                />
              </label>
              <button
                className="primary-button"
                type="button"
                disabled={!accountId || !file || importing}
                onClick={() => void submitImport('preview')}
              >
                {importing ? 'Working…' : 'Preview'}
              </button>
            </div>
            )}

            {!preview && previewHint && !importError && <p className="import-hint">{previewHint}</p>}
            {confirmHint && <p className="import-hint" role="status">{confirmHint}</p>}
            {importError && <p className="message message--error" role="alert">{importError}</p>}
            {preview && (
              <div className="preview-content">
                <div className="kpi-strip">
                  <div className="kpi-card">
                    <div className="kpi-card-header">
                      <span className="kpi-label">Source Rows</span>
                      <span className="kpi-icon-badge" aria-hidden="true">
                        <svg width="18" height="18" viewBox="0 0 20 20" fill="none">
                          <rect x="3" y="3" width="14" height="14" rx="3" fill="var(--info-soft)" stroke="var(--chart-needs)" strokeWidth="1.5" />
                          <path d="M6.5 7h7M6.5 10h7M6.5 13h4.5" stroke="var(--chart-needs)" strokeWidth="1.5" strokeLinecap="round" />
                        </svg>
                      </span>
                    </div>
                    <strong className="kpi-value">{preview.source_row_count}</strong>
                    <span className="kpi-sub">{preview.filename} · {preview.account.name}</span>
                  </div>
                  <div className="kpi-card kpi-card--accent">
                    <div className="kpi-card-header">
                      <span className="kpi-label">New Rows</span>
                      <span className="kpi-icon-badge" aria-hidden="true">
                        <svg width="18" height="18" viewBox="0 0 20 20" fill="none">
                          <circle cx="10" cy="10" r="7" fill="var(--accent-soft)" stroke="var(--accent)" strokeWidth="1.5" />
                          <path d="M10 6.75v6.5M6.75 10h6.5" stroke="var(--accent)" strokeWidth="1.75" strokeLinecap="round" />
                        </svg>
                      </span>
                    </div>
                    <strong className="kpi-value">{preview.new_row_count}</strong>
                    <span className="kpi-sub">{preview.already_represented_row_count} already represented</span>
                  </div>
                  <div className={`kpi-card ${preview.possible_duplicate_row_count > 0 ? 'kpi-card--warning' : ''}`}>
                    <div className="kpi-card-header">
                      <span className="kpi-label">Possible Duplicates</span>
                      <span className="kpi-icon-badge" aria-hidden="true">
                        <svg width="18" height="18" viewBox="0 0 20 20" fill="none">
                          <rect x="3.5" y="5.5" width="9" height="10" rx="2" fill="var(--warning-soft)" stroke="var(--warning)" strokeWidth="1.5" />
                          <rect x="7.5" y="3.5" width="9" height="10" rx="2" fill="var(--surface)" stroke="var(--warning)" strokeWidth="1.5" />
                        </svg>
                      </span>
                    </div>
                    <strong className="kpi-value">{preview.possible_duplicate_row_count}</strong>
                    <span className="kpi-sub">{preview.invalid_row_count} invalid</span>
                  </div>
                  <div className={`kpi-card ${preview.review_required_row_count > 0 ? 'kpi-card--warning' : ''}`}>
                    <div className="kpi-card-header">
                      <span className="kpi-label">Needs Review</span>
                      <span className="kpi-icon-badge" aria-hidden="true">
                        <svg width="18" height="18" viewBox="0 0 20 20" fill="none">
                          <path d="M10 3.25l7 12.5H3l7-12.5z" fill="var(--warning-soft)" stroke="var(--warning)" strokeWidth="1.5" strokeLinejoin="round" />
                          <path d="M10 8v3.25M10 13.5h.01" stroke="var(--warning)" strokeWidth="1.75" strokeLinecap="round" />
                        </svg>
                      </span>
                    </div>
                    <strong className="kpi-value">{preview.review_required_row_count}</strong>
                    <span className="kpi-sub">{preview.valid_row_count} valid rows</span>
                  </div>
                </div>
                <p className="preview-summary">
                  {preview.filename} · {preview.source_row_count} source rows · {preview.new_row_count} new ·
                  {' '}{preview.already_represented_row_count} already represented ·
                  {' '}{preview.possible_duplicate_row_count} possible duplicates ·
                  {' '}{preview.invalid_row_count} invalid ·
                  {' '}{preview.review_required_row_count} need review · Account: {preview.account.name}
                </p>
                {preview.already_imported && (
                  <p className="preview-warning" role="alert">This exact file was already imported to this account.</p>
                )}
                {preview.valid_rows.length > 0 && (
                  <div className="table-scroll">
                    <table>
                      <thead>
                        <tr>
                          <th scope="col">Row / date</th>
                          <th scope="col">Description</th>
                          <th scope="col">Source type</th>
                          <th scope="col">Classification</th>
                          <th scope="col">Import result</th>
                          <th scope="col" className="amount-cell">Amount</th>
                        </tr>
                      </thead>
                      <tbody>
                        {preview.valid_rows.map((row) => (
                          <tr key={row.source_row}>
                            <td>{row.source_row} · {row.date}</td>
                            <td>{row.raw_description}</td>
                            <td>{row.source_type}</td>
                            <td>
                              <span className={`classification classification--${row.classification.toLowerCase()}`}>
                                {classificationLabels[row.classification]}
                              </span>
                            </td>
                            <td>
                              <span className={`overlap-status overlap-status--${row.overlap_status.toLowerCase()}`}>
                                {row.overlap_status === 'ALREADY_REPRESENTED'
                                  ? 'Already represented'
                                  : row.overlap_status === 'POSSIBLE_DUPLICATE'
                                    ? 'Possible duplicate'
                                    : 'New'}
                              </span>
                              {row.duplicate_candidates.length > 0 && (
                                <small className="candidate-summary">
                                  {row.overlap_reason} Compared with {row.duplicate_candidates.map((candidate) =>
                                    `#${candidate.id} ${candidate.account} · ${candidate.date} · ${candidate.raw_description} · ${formatAmount(candidate.amount)} · ${candidate.source_type}`,
                                  ).join(' | ')}
                                </small>
                              )}
                              {row.categorization.status === 'AUTO_RESOLVED' && row.categorization.classification && (
                                <small className="categorization-preview">
                                  Auto-resolved: {outcomeText(row.categorization)}
                                  {' '}({row.categorization.rules.length} matching rule{row.categorization.rules.length === 1 ? '' : 's'}:
                                  {' '}{row.categorization.rules.map(ruleText).join('; ')})
                                </small>
                              )}
                              {row.categorization.status === 'RULE_CONFLICT' && (
                                <small className="categorization-conflict">
                                  Rule conflict — requires Review. Conflicting rules:
                                  {' '}{row.categorization.rules.map((rule) => `${ruleText(rule)} → ${outcomeText(rule)}`).join(' | ')}
                                </small>
                              )}
                            </td>
                            <td className={`amount-cell ${row.amount.startsWith('-') ? 'amount-negative' : 'amount-positive'}`}>
                              {formatAmount(row.amount)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                {preview.review_required_row_count > 0 && (
                  <p className="preview-warning">
                    Rows needing attention will be imported into Review; possible duplicates remain excluded from future totals until resolved.
                  </p>
                )}
                {preview.invalid_rows.length > 0 && (
                  <div className="invalid-rows">
                    <h3>Rows that prevent this import</h3>
                    {preview.invalid_rows.map((row) => (
                      <p key={row.source_row}>Row {row.source_row}: {row.errors.join(' ')}</p>
                    ))}
                    <p>No rows will be imported until every row is valid.</p>
                  </div>
                )}
              </div>
            )}
          </section>
        )}

        {reportsVisited && <div hidden={view !== 'reports'}>
          <Reports reload={reload} onReview={(reason, start, end) => {
            setReviewReason(reason)
            setReviewDates({ start, end })
            setView('review')
          }} />
        </div>}
        {view === 'reports'
          ? null
          : view === 'categories'
          ? <Categories />
          : view === 'rules'
            ? <Rules reload={reload} onChanged={() => setReload((current) => current + 1)} />
            : view === 'accounts'
              ? <Accounts reload={reload} onChanged={() => setReload((current) => current + 1)} />
              : view === 'debts'
                ? <Debts reload={reload} />
                : view === 'recurring'
                  ? <Recurring reload={reload} />
                  : previewing
                    ? null
                    : <TransactionTable key={`${view}-${reviewReason}-${reviewDates.start}-${reviewDates.end}`} mode={view} reload={reload}
                        initialReason={view === 'review' ? reviewReason : ''}
                        initialStartDate={view === 'review' ? reviewDates.start : ''}
                        initialEndDate={view === 'review' ? reviewDates.end : ''} />}
      </main>
    </div>
  )
}

export default App

// Run against a fresh disposable database and Chrome with --remote-debugging-port=19222.
import assert from 'node:assert/strict'

const port = process.env.PENNYTRACKER_BROWSER_PORT ?? '15173'
const cdp = process.env.CHROME_DEBUG_PORT ?? '19222'
const page = (await (await fetch(`http://127.0.0.1:${cdp}/json`)).json())
  .find(item => item.type === 'page' && new URL(item.url).port === port)
assert.ok(page, 'Open the disposable frontend in Chrome first.')
const socket = new WebSocket(page.webSocketDebuggerUrl)
await new Promise(resolve => socket.addEventListener('open', resolve, { once: true }))
let id = 0
const pending = new Map()
socket.addEventListener('message', event => {
  const message = JSON.parse(event.data)
  if (!pending.has(message.id)) return
  const { resolve, reject } = pending.get(message.id)
  pending.delete(message.id)
  message.error ? reject(new Error(message.error.message)) : resolve(message.result)
})
async function command(method, params = {}) {
  return new Promise((resolve, reject) => {
    const requestId = ++id
    pending.set(requestId, { resolve, reject })
    socket.send(JSON.stringify({ id: requestId, method, params }))
  })
}
async function evaluate(expression) {
  const result = await command('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })
  if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails))
  return result.result.value
}
async function waitFor(expression, label) {
  for (let count = 0; count < 75; count++) {
    if (await evaluate(`Boolean(${expression})`)) return
    await new Promise(resolve => setTimeout(resolve, 200))
  }
  throw new Error(`${label}: ${await evaluate('document.body.innerText')}`)
}
async function click(expression) {
  assert.ok(await evaluate(`(() => { const e = ${expression}; if (!e) return false; e.click(); return true })()`), expression)
}
async function field(label, value, container = '.accounts-panel > form') {
  assert.ok(await evaluate(`(() => {
    const e = [...document.querySelectorAll(${JSON.stringify(container)} + ' label')]
      .find(x => x.textContent.trim().startsWith(${JSON.stringify(label)}))?.querySelector('input,select')
    if (!e) return false
    const proto = e.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(e, ${JSON.stringify(value)})
    e.dispatchEvent(new Event(e.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true }))
    return true
  })()`), label)
}
async function button(text, container = 'body') {
  await click(`[...document.querySelectorAll(${JSON.stringify(container)} + ' button')].find(e => e.textContent.trim() === ${JSON.stringify(text)})`)
}
async function nav(text) { await button(text, 'nav') }
async function create(name, type, destination = false) {
  await field('Name', name)
  await field('Type', type)
  if (destination) await click(`document.querySelector('.accounts-panel > form input[type=checkbox]')`)
  await button('Add account', '.accounts-panel > form')
  await waitFor(`Array.from(document.querySelectorAll('tr')).some(e => e.textContent.includes(${JSON.stringify(name)}))`, `create ${name}`)
}
async function rowButton(name, text) {
  await waitFor(`Array.from(document.querySelectorAll('.accounts-panel tr')).some(e => e.textContent.includes(${JSON.stringify(name)}))`, `account row ${name}`)
  await click(`[...document.querySelectorAll('tr')].find(e => e.textContent.includes(${JSON.stringify(name)}))?.querySelectorAll('button')[${text === 'Edit' ? 0 : 1}]`)
}
try {
  await evaluate('location.reload(); true')
  await waitFor(`document.querySelector('h1')?.textContent === 'Transactions'`, 'app')
  assert.deepEqual(await evaluate(`fetch('/api/accounts/').then(r => r.json())`), [], 'Use a fresh disposable database.')
  await evaluate(`window.confirm = () => true`)
  await button('Import CSV')
  await waitFor(`document.body.innerText.includes('You need an active account before importing transactions.')`, 'empty import')
  assert.equal(await evaluate(`Boolean(document.querySelector('.import-controls select'))`), false)
  await button('Go to Accounts')
  await waitFor(`document.body.innerText.includes('Create an account before importing transaction CSVs.')`, 'empty accounts')
  await create('Example Checking', 'CHECKING')
  await create('Example Savings', 'SAVINGS', true)
  await create('Example Credit Card', 'CREDIT_CARD')
  assert.equal(await evaluate(`Boolean(document.querySelector('.accounts-panel > form input[type=checkbox]'))`), false)
  await rowButton('Example Credit Card', 'Edit')
  assert.equal(await evaluate(`Boolean(document.querySelector('.account-edit input[type=checkbox]'))`), false)
  await button('Cancel', '.account-edit')
  await rowButton('Example Checking', 'Edit')
  await field('Name', 'Example Renamed', '.account-edit')
  await field('Type', 'OTHER', '.account-edit')
  await button('Save changes', '.account-edit')
  await waitFor(`document.body.innerText.includes('Example Renamed') && !document.querySelector('.account-edit')`, 'edit account')
  await nav('Transactions')
  await button('Import CSV')
  await waitFor(`document.querySelector('.import-controls select')?.options.length === 4`, 'active import options')
  const accounts = await evaluate(`fetch('/api/accounts/').then(r => r.json())`)
  const checking = accounts.find(item => item.name === 'Example Renamed')
  assert.equal(accounts.find(item => item.name === 'Example Savings').is_savings_destination, true)
  assert.equal(accounts.find(item => item.name === 'Example Credit Card').is_savings_destination, false)
  await field('Account', String(checking.id), '.import-controls')
  await evaluate(`(() => {
    const input = document.querySelector('.import-controls input[type=file]')
    const transfer = new DataTransfer()
    transfer.items.add(new File(['Unsupported\\nvalue\\n'], 'invalid.csv', {type:'text/csv'}))
    input.files = transfer.files
    input.dispatchEvent(new Event('change', {bubbles:true}))
  })()`)
  await button('Preview', '.import-controls')
  await waitFor(`document.body.innerText.includes('Unsupported CSV structure')`, 'import validation error')
  assert.equal(await evaluate(`Boolean(document.querySelector('.import-controls input[type=file]'))`), true, 'import controls remain available after an error')
  await evaluate(`(() => {
    const input = document.querySelector('.import-controls input[type=file]')
    const transfer = new DataTransfer()
    transfer.items.add(new File(['Date,Description,Type,Amount,Status\\n2026-01-15,EXAMPLE PURCHASE,DEBIT_CARD,-12.34,Posted\\n'], 'example.csv', {type:'text/csv'}))
    input.files = transfer.files
    input.dispatchEvent(new Event('change', {bubbles:true}))
  })()`)
  await button('Preview', '.import-controls')
  await waitFor(`document.body.innerText.includes('Import preview')`, 'preview')
  await button('Confirm import')
  await waitFor(`document.body.innerText.includes('EXAMPLE PURCHASE') && !document.body.innerText.includes('Import preview')`, 'import')
  await nav('Accounts')
  await rowButton('Example Renamed', 'Archive')
  await waitFor(`document.querySelectorAll('.account-group')[1].textContent.includes('Example Renamed')`, 'archived')
  await nav('Transactions')
  await waitFor(`Array.from(document.querySelectorAll('.ledger-filters select option')).some(e => e.value === '${checking.id}' && e.textContent.includes('Example Renamed'))`, 'archived account filter')
  await field('Account', String(checking.id), '.ledger-filters')
  await waitFor(`document.body.innerText.includes('EXAMPLE PURCHASE')`, 'archived transaction')
  await nav('Review')
  await waitFor(`Array.from(document.querySelectorAll('.ledger-filters select option')).some(e => e.value === '${checking.id}')`, 'archived review filter')
  await field('Account', String(checking.id), '.ledger-filters')
  await waitFor(`document.body.innerText.includes('EXAMPLE PURCHASE')`, 'archived review transaction')
  await nav('Transactions')
  await button('Import CSV')
  await waitFor(`document.querySelector('.import-controls select')?.options.length === 3`, 'archived excluded')
  assert.equal(await evaluate(`Array.from(document.querySelector('.import-controls select').options).some(e => e.value === '${checking.id}')`), false)
  await nav('Accounts')
  await rowButton('Example Savings', 'Archive')
  await waitFor(`document.querySelectorAll('.account-group')[1].textContent.includes('Example Savings')`, 'archive savings')
  await rowButton('Example Credit Card', 'Archive')
  await waitFor(`document.querySelectorAll('.account-group')[1].textContent.includes('Example Credit Card')`, 'archive credit card')
  await nav('Transactions')
  await button('Import CSV')
  await waitFor(`document.body.innerText.includes('You need an active account before importing transactions.')`, 'all archived')
  await button('Go to Accounts')
  await rowButton('Example Renamed', 'Reactivate')
  await waitFor(`document.querySelectorAll('.account-group')[0].textContent.includes('Example Renamed')`, 'reactivate')
  await nav('Transactions')
  await button('Import CSV')
  await waitFor(`Array.from(document.querySelectorAll('.import-controls select option')).some(e => e.value === '${checking.id}')`, 'reactivated import')
  await nav('Accounts')
  for (const theme of ['light', 'dark', 'system']) {
    await field('Theme', theme, '.sidebar-footer')
    assert.equal(await evaluate(`document.documentElement.dataset.theme`), theme)
  }
  await command('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: 'dark' }] })
  const darkSurface = await evaluate(`getComputedStyle(document.querySelector('.accounts-panel')).backgroundColor`)
  await command('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: 'light' }] })
  const lightSurface = await evaluate(`getComputedStyle(document.querySelector('.accounts-panel')).backgroundColor`)
  assert.notEqual(darkSurface, lightSurface, 'System theme follows the operating system')
  await command('Emulation.setDeviceMetricsOverride', { width: 375, height: 812, deviceScaleFactor: 1, mobile: false })
  assert.equal(await evaluate(`document.documentElement.scrollWidth <= window.innerWidth`), true, 'narrow layout overflows')
  console.log('PASS: account onboarding, editing, import, archive history, reactivation, themes, narrow layout')
} finally {
  await command('Emulation.clearDeviceMetricsOverride')
  await command('Emulation.setEmulatedMedia', { features: [] })
  socket.close()
}

// Use a disposable empty database seeded with review-browser-fixtures.py, a running frontend,
// and a Chrome page open to it with --remote-debugging-port=19222.
// Run: npm --prefix frontend run test:review-browser
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
async function evaluate(expression) {
  const result = await new Promise((resolve, reject) => {
    const requestId = ++id
    pending.set(requestId, { resolve, reject })
    socket.send(JSON.stringify({ id: requestId, method: 'Runtime.evaluate', params: {
      expression, returnByValue: true, awaitPromise: true,
    } }))
  })
  if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails))
  return result.result.value
}
async function waitFor(expression, label) {
  for (let count = 0; count < 75; count++) {
    if (await evaluate(expression)) return
    await new Promise(resolve => setTimeout(resolve, 200))
  }
  throw new Error(`${label}: ${await evaluate('document.body.innerText')}`)
}
async function click(expression) {
  assert.ok(await evaluate(`(() => {const e=${expression}; if (!e) return false; e.click(); return true})()`), expression)
}
async function select(label, value, container = '.transaction-editor') {
  assert.ok(await evaluate(`(() => {
    const e=[...document.querySelectorAll('${container} label')].find(e => e.textContent.trim().startsWith('${label}'))?.querySelector('select');
    if (!e) return false; e.value='${value}'; e.dispatchEvent(new Event('change',{bubbles:true})); return true
  })()`), label)
}
async function text(label, value, container = '.transaction-editor') {
  assert.ok(await evaluate(`(() => {
    const e=[...document.querySelectorAll('${container} label')].find(e => e.textContent.trim().startsWith('${label}'))?.querySelector('input,textarea');
    if (!e) return false;
    Object.getOwnPropertyDescriptor(e.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype, 'value').set.call(e, ${JSON.stringify(value)});
    e.dispatchEvent(new Event('input',{bubbles:true})); return true
  })()`), label)
}
async function edit(name) {
  await waitFor(`Array.from(document.querySelectorAll('tr')).some(e => e.textContent.includes('${name}'))`, name)
  await click(`[...document.querySelectorAll('tr')].find(e => e.textContent.includes('${name}')).querySelector('button')`)
}
async function save() {
  await click(`[...document.querySelectorAll('.transaction-editor button')].find(e => e.textContent.trim() === 'Save changes')`)
  await waitFor(`!document.querySelector('.transaction-editor')`, 'save closed')
}
async function record(name) {
  return evaluate(`fetch('/api/transactions/?search=${encodeURIComponent(name)}').then(r=>r.json()).then(r=>r.results.find(x=>x.raw_description===${JSON.stringify(name)}))`)
}
try {
  await evaluate('location.reload(); true')
  await new Promise(resolve => setTimeout(resolve, 500))
  await waitFor(`document.body.innerText.includes('PennyTracker')`, 'app')
  const categories = await evaluate(`fetch('/api/subcategories/').then(r=>r.json())`)
  const rules = await evaluate(`fetch('/api/categorization-rules/').then(r=>r.json())`)
  const needs = categories.find(item => item.name === 'Synthetic needs')
  const archived = categories.find(item => item.name === 'Synthetic archived')
  const savings = rules.find(item => item.savings_effect === 'CONTRIBUTION' && item.match_value === 'EXAMPLE MOVE')
  const ordinary = rules.find(item => item.savings_effect === 'NONE' && item.match_value === 'EXAMPLE MOVE')
  assert.ok(needs && archived && savings && ordinary, 'Seed a disposable database before running.')
  await evaluate(`window.__requests=[]; window.__originalFetch=window.fetch; window.fetch=(url,init)=>{if(init?.method && init.method!=='GET') window.__requests.push({url:String(url),method:init.method,body:init.body}); return window.__originalFetch(url,init)}; true`)
  await click(`[...document.querySelectorAll('nav button')].find(e => e.textContent.trim() === 'Review')`)
  await waitFor(`document.body.innerText.includes('EXAMPLE INLINE')`, 'review')
  const apply = await evaluate(`(async()=>{const t=await fetch('/api/csrf/').then(r=>r.json()); const r=await fetch('/api/categorization-rules/${savings.id}/apply-to-pending/',{method:'POST',headers:{'X-CSRFToken':t.csrfToken}}); return [r.status,await r.json()]})()`)
  assert.equal(apply[0], 200)
  assert.equal(apply[1].updated_count, 0)
  for (const name of ['EXAMPLE MOVE 123', 'EXAMPLE MOVE MANUAL']) {
    const tx = await record(name)
    assert.equal(tx.savings_effect, 'NONE')
    assert.equal(tx.subcategory, null)
    assert.equal(tx.review_status, 'PENDING')
  }
  assert.equal((await record('EXAMPLE MOVE 123')).categorized_by_rule, ordinary.id)

  await click(`[...document.querySelectorAll('tr')].find(e=>e.textContent.includes('EXAMPLE BULK CLASS')).querySelector('input')`)
  await select('Classification', 'INCOME', '.bulk-toolbar')
  await click(`[...document.querySelectorAll('.bulk-toolbar button')].find(e=>e.textContent.trim()==='Set classification')`)
  await waitFor(`document.body.innerText.includes('Updated 1 transactions')`, 'bulk classification')
  let tx = await record('EXAMPLE BULK CLASS')
  assert.equal(tx.categorization_rule_conflict, false)
  assert.equal(tx.review_status, 'PENDING')
  assert.equal(tx.duplicate_resolution, 'UNRESOLVED')
  await waitFor(`Boolean([...document.querySelectorAll('tr')].find(e=>e.textContent.includes('EXAMPLE BULK CATEGORY')))`, 'bulk category row')
  await click(`[...document.querySelectorAll('tr')].find(e=>e.textContent.includes('EXAMPLE BULK CATEGORY')).querySelector('input')`)
  await select('Subcategory', String(needs.id), '.bulk-toolbar')
  await click(`[...document.querySelectorAll('.bulk-toolbar button')].find(e=>e.textContent.trim()==='Assign subcategory')`)
  await waitFor(`!document.querySelector('.bulk-toolbar')`, 'bulk category')
  tx = await record('EXAMPLE BULK CATEGORY')
  assert.equal(tx.categorization_rule_conflict, false)
  assert.equal(tx.review_status, 'PENDING')

  await edit('EXAMPLE INLINE')
  await click(`[...document.querySelectorAll('.transaction-editor label')].find(e=>e.textContent.includes('Resolve this rule conflict')).querySelector('input')`)
  await click(`document.querySelector('.inline-category-create summary')`)
  await text('Name', 'Synthetic inline created', '.inline-category-create')
  await click(`[...document.querySelectorAll('.inline-category-create button')].find(e=>e.textContent.includes('Create and assign'))`)
  await waitFor(`document.body.innerText.includes('Subcategory created and assigned.')`, 'inline creation')
  assert.equal(await evaluate(`document.body.innerText.includes('Resolve this rule conflict')`), false)
  await text('Notes', 'Synthetic inline note')
  await save()
  let request = await evaluate(`window.__requests.filter(x=>x.url.includes('review-save')).at(-1)`)
  assert.equal(JSON.parse(request.body).resolve_rule_conflict, undefined)
  assert.equal((await record('EXAMPLE INLINE')).notes, 'Synthetic inline note')

  await edit('EXAMPLE REMEMBER')
  await select('Classification', 'INCOME')
  await click(`[...document.querySelectorAll('.transaction-editor label')].find(e=>e.textContent.includes('Remember this outcome')).querySelector('input')`)
  await select('Classification', 'UNKNOWN')
  assert.equal(await evaluate(`Boolean(document.querySelector('.remember-rule'))`), false)
  await select('Classification', 'INCOME')
  assert.equal(await evaluate(`document.querySelector('.remember-rule input').checked`), false)
  await select('Classification', 'UNKNOWN')
  await text('Notes', 'Still undecided')
  await save()
  request = await evaluate(`window.__requests.filter(x=>x.url.includes('/transactions/') && x.method==='POST').at(-1)`)
  assert.ok(request.url.includes('review-save/'))
  assert.equal((await record('EXAMPLE REMEMBER')).review_status, 'PENDING')
  await edit('EXAMPLE REMEMBER')
  await select('Classification', 'TRANSFER')
  await select('Savings activity', 'CONTRIBUTION')
  assert.equal(await evaluate(`Array.from(document.querySelector('.transaction-editor select[required]').options).some(e=>e.value==='${archived.id}')`), false)
  await click(`[...document.querySelectorAll('.transaction-editor button')].find(e=>e.textContent.trim()==='Cancel')`)

  await edit('EXAMPLE ARCHIVED')
  assert.equal(await evaluate(`document.querySelector('.transaction-editor select[required]').selectedOptions[0].textContent.includes('archived')`), true)
  assert.equal(await evaluate(`document.querySelector('.transaction-editor').checkValidity()`), true)
  await text('Notes', 'Synthetic retained note')
  await save()
  await edit('EXAMPLE ARCHIVED')
  await text('Clean merchant', 'Synthetic retained merchant')
  await save()
  tx = await record('EXAMPLE ARCHIVED')
  assert.equal(tx.subcategory, archived.id)
  assert.equal(tx.notes, 'Synthetic retained note')
  assert.equal(tx.merchant, 'Synthetic retained merchant')
  await edit('EXAMPLE ARCHIVED')
  await select('Savings activity', 'WITHDRAWAL')
  assert.equal(await evaluate(`document.querySelector('.transaction-editor select[required]').value`), '')
  assert.equal(await evaluate(`document.querySelector('.transaction-editor').checkValidity()`), false)
  await click(`[...document.querySelectorAll('.transaction-editor button')].find(e=>e.textContent.trim()==='Cancel')`)

  for (const [status, name] of [['REVIEWED', 'EXAMPLE REVIEWED'], ['NOT_REQUIRED', 'EXAMPLE NOT REQUIRED']]) {
    await select('Review status', status, '.ledger-filters')
    await edit(name)
    assert.equal(await evaluate(`Boolean(document.querySelector('.remember-rule'))`), false)
    await text('Notes', `Synthetic ${status} note`)
    await save()
    request = await evaluate(`window.__requests.filter(x=>x.url.includes('/transactions/') && x.method==='PATCH').at(-1)`)
    assert.ok(request)
    tx = await record(name)
    assert.equal(tx.notes, `Synthetic ${status} note`)
    assert.equal(tx.review_status, status)
  }
  console.log('PASS: Transfer protection, bulk conflicts, inline conflict, Remember reset, archived edits, historical Review filters')
} finally {
  socket.close()
}

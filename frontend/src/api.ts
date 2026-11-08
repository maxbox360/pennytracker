let csrfRequest: Promise<string> | null = null

function readCsrfCookie() {
  const cookie = document.cookie
    .split('; ')
    .find((part) => part.startsWith('csrftoken='))
  return cookie ? decodeURIComponent(cookie.slice('csrftoken='.length)) : null
}

async function getCsrfToken() {
  const cookieToken = readCsrfCookie()
  if (cookieToken) return cookieToken
  if (!csrfRequest) {
    csrfRequest = fetch('/api/csrf/', { credentials: 'same-origin' })
      .then(async (response) => {
        if (!response.ok) throw new Error(`CSRF initialization failed (${response.status})`)
        const result: { csrfToken: string } = await response.json()
        return readCsrfCookie() ?? result.csrfToken
      })
      .finally(() => {
        csrfRequest = null
      })
  }
  return csrfRequest
}

export async function apiFetch(input: RequestInfo | URL, init: RequestInit = {}) {
  const method = (init.method ?? 'GET').toUpperCase()
  const headers = new Headers(init.headers)
  const unsafe = !['GET', 'HEAD', 'OPTIONS', 'TRACE'].includes(method)

  if (unsafe) headers.set('X-CSRFToken', await getCsrfToken())
  return fetch(input, {
    ...init,
    headers,
    credentials: 'same-origin',
  })
}

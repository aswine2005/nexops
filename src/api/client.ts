const TOKEN_KEY = 'nexops-session-token'
const API_BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, '') ?? ''

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

let unauthorizedHandler: (() => void) | null = null

export function onUnauthorized(handler: () => void) { unauthorizedHandler = handler }

export function getToken(): string | null {
  try { return localStorage.getItem(TOKEN_KEY) } catch { return null }
}

export function setToken(token: string | null) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
  } catch { /* storage may be unavailable; the session then lasts for this tab only */ }
}

export async function api<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const headers = new Headers(init.headers)
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  let body = init.body
  if (init.json !== undefined) {
    headers.set('Content-Type', 'application/json')
    body = JSON.stringify(init.json)
  }
  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, { ...init, headers, body })
  } catch {
    throw new ApiError(0, 'NexOps API is unreachable. Check that the backend is running.')
  }
  if (response.status === 401 && !path.startsWith('/api/auth/login')) {
    setToken(null)
    unauthorizedHandler?.()
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { detail?: unknown } | null
    const detail = payload?.detail
    const message = typeof detail === 'string' ? detail
      : Array.isArray(detail) ? detail.map((item: { msg?: string; loc?: unknown[] }) => `${(item.loc ?? []).slice(1).join('.')}: ${item.msg}`).join('; ')
        : `Request failed with HTTP ${response.status}`
    throw new ApiError(response.status, message)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export function query(params: Record<string, string | number | null | undefined>): string {
  const search = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => { if (value !== null && value !== undefined && value !== '') search.set(key, String(value)) })
  const text = search.toString()
  return text ? `?${text}` : ''
}

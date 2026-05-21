const DEFAULT_API_BASE = '/v1'

export function getApiBaseUrl(): string {
  const fromUrl = (import.meta.env.VITE_API_URL ?? '').trim().replace(/\/$/, '')
  const fromLegacy = (import.meta.env.VITE_API_BASE_URL ?? '').trim().replace(/\/$/, '')
  const explicit = fromUrl || fromLegacy
  if (explicit) {
    if (!explicit.startsWith('http://') && !explicit.startsWith('https://')) {
      return explicit.startsWith('/') ? explicit : `/${explicit}`
    }
    return explicit
  }
  return DEFAULT_API_BASE
}

function formatValidationDetail(detail: unknown): string {
  if (Array.isArray(detail)) {
    const parts = detail.map((item) => {
      if (item && typeof item === 'object' && 'msg' in item) {
        const loc = Array.isArray((item as { loc?: unknown }).loc)
          ? (item as { loc: unknown[] }).loc.join('.')
          : ''
        const msg = String((item as { msg?: unknown }).msg ?? item)
        return loc ? `${loc}: ${msg}` : msg
      }
      return String(item)
    })
    return parts.join('; ') || 'Request failed'
  }
  if (detail && typeof detail === 'object') {
    return JSON.stringify(detail)
  }
  return String(detail ?? 'Request failed')
}

function gatewayErrorMessage(status: number): string | null {
  if (status === 502 || status === 504) {
    return (
      `Cannot reach the API backend (${getApiBaseUrl()}). ` +
      'Start it from the project root: python main.py ' +
      '(or: python -m uvicorn app.main:app --reload --port 8000)'
    )
  }
  if (status === 503) {
    return (
      'API backend is unavailable. If auth endpoints fail, set ' +
      'COGNITO_USER_POOL_ID and COGNITO_APP_CLIENT_ID in the project .env file.'
    )
  }
  return null
}

export function parseApiErrorBody(
  data: unknown,
  status: number,
  statusText: string,
): string {
  const gateway = gatewayErrorMessage(status)
  if (gateway) return gateway

  if (typeof data === 'string' && data.trim()) {
    const lower = data.toLowerCase()
    if (lower.includes('bad gateway') || lower.includes('gateway timeout')) {
      return gatewayErrorMessage(502) ?? data
    }
    return data
  }

  if (data && typeof data === 'object' && 'detail' in data) {
    let detail = formatValidationDetail((data as { detail?: unknown }).detail)
    if (status === 503 && detail.toLowerCase().includes('not initialized')) {
      detail += ' Set COGNITO_USER_POOL_ID and COGNITO_APP_CLIENT_ID in .env.'
    }
    return detail
  }

  if (statusText) return statusText
  return status > 0 ? `HTTP ${status}` : 'Request failed'
}

export function connectionErrorMessage(error: unknown): string {
  const msg = error instanceof Error ? error.message : String(error)
  if (
    msg.includes('Failed to fetch') ||
    msg.includes('NetworkError') ||
    msg.includes('Connection refused') ||
    msg.includes('timed out')
  ) {
    return `Cannot reach API at ${getApiBaseUrl()}. Start the backend: python -m uvicorn app.main:app --reload --port 8000`
  }
  return msg
}

export function getRtkErrorMessage(error: unknown): string {
  if (!error || typeof error !== 'object') {
    return String(error ?? 'Request failed')
  }
  if ('error' in error && typeof (error as { error?: unknown }).error === 'string') {
    return (error as { error: string }).error
  }
  if ('data' in error) {
    const data = (error as { data?: unknown }).data
    if (typeof data === 'string' && data) return data
    if (data && typeof data === 'object' && 'detail' in data) {
      return formatValidationDetail((data as { detail?: unknown }).detail)
    }
  }
  if ('message' in error && typeof (error as { message?: unknown }).message === 'string') {
    return (error as { message: string }).message
  }
  return 'Request failed'
}

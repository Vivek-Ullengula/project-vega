import { emptySessionUser, type SessionUser } from '../types/auth'

export const AUTH_STORAGE_KEY = 'vega_session_user'
export const AUTH_SESSION_EXPIRED_EVENT = 'vega:auth-session-expired'
export const AUTH_STATUS_STORAGE_KEY = 'vega_auth_status'
export const SESSION_EXPIRED_MESSAGE =
  'Your session has expired. Please log in again.'

export function loadStoredUser(): SessionUser {
  try {
    const raw = localStorage.getItem(AUTH_STORAGE_KEY)
    if (!raw) return emptySessionUser()
    const parsed = JSON.parse(raw) as SessionUser
    if (parsed?.authenticated && parsed.token) return parsed
  } catch {
    /* ignore corrupt storage */
  }
  return emptySessionUser()
}

export function persistUser(user: SessionUser) {
  if (user.authenticated) {
    localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(user))
  } else {
    localStorage.removeItem(AUTH_STORAGE_KEY)
  }
}

export function expireStoredAuthSession() {
  try {
    localStorage.removeItem(AUTH_STORAGE_KEY)
    sessionStorage.setItem(AUTH_STATUS_STORAGE_KEY, SESSION_EXPIRED_MESSAGE)
  } catch {
    /* ignore unavailable storage */
  }

  if (typeof window !== 'undefined') {
    window.dispatchEvent(new Event(AUTH_SESSION_EXPIRED_EVENT))
  }
}

export function isExpiredTokenMessage(message: string): boolean {
  const normalized = message.toLowerCase()
  return (
    normalized.includes('invalid token') &&
    normalized.includes('signature has expired')
  )
}

export function consumeAuthStatusMessage(): string {
  try {
    const message = sessionStorage.getItem(AUTH_STATUS_STORAGE_KEY) ?? ''
    sessionStorage.removeItem(AUTH_STATUS_STORAGE_KEY)
    return message
  } catch {
    return ''
  }
}

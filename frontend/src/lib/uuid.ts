/**
 * Generate a UUID v4 string.
 *
 * `crypto.randomUUID()` is only available in **secure contexts** (HTTPS or
 * localhost).  When the app is served over plain HTTP (e.g. an internal IP),
 * we fall back to `crypto.getRandomValues()` which works everywhere.
 */
export function uuid(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }

  // Fallback — crypto.getRandomValues is available in all contexts
  return '10000000-1000-4000-8000-100000000000'.replace(/[018]/g, (c) =>
    (+c ^ (crypto.getRandomValues(new Uint8Array(1))[0] & (15 >> (+c / 4)))).toString(16),
  )
}

import { useEffect, useState, type FormEvent } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../../context/AuthContext'
import { consumeAuthStatusMessage } from '../../lib/authSession'
import {
  btnPrimaryClass,
  fieldClass,
  formClass,
  inputClass,
  labelClass,
} from '../../lib/styles'

export function LoginForm() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const from =
    (location.state as { from?: { pathname?: string } } | null)?.from?.pathname ?? '/chat'
  const redirectPath = from === '/' || from.startsWith('/chat/') ? '/chat' : from
  const initialStatus =
    (location.state as { status?: string } | null)?.status ?? ''
  const initialToast = consumeAuthStatusMessage()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [status, setStatus] = useState(initialStatus)
  const [toast, setToast] = useState(initialToast)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!toast) return
    const timeoutId = window.setTimeout(() => setToast(''), 8000)
    return () => window.clearTimeout(timeoutId)
  }, [toast])

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setLoading(true)
    setStatus('')
    const result = await login(email, password)
    if (result.startsWith('Welcome')) {
      navigate(redirectPath, { replace: true })
    } else {
      setStatus(result)
    }
    setLoading(false)
  }

  const isError = status.startsWith('Login failed') || status.startsWith('Please enter')

  return (
    <>
      {toast ? (
        <div
          role="alert"
          className="fixed right-4 top-4 z-50 flex w-[min(calc(100vw-2rem),360px)] items-start gap-3 rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm leading-relaxed text-amber-950 shadow-lg"
        >
          <span className="min-w-0 flex-1">{toast}</span>
          <button
            type="button"
            className="-mr-1 rounded px-1.5 py-0.5 text-amber-900 hover:bg-amber-100"
            aria-label="Dismiss notification"
            onClick={() => setToast('')}
          >
            x
          </button>
        </div>
      ) : null}

      <form className={formClass} onSubmit={handleSubmit}>
      <label className={fieldClass}>
        <span className={labelClass}>Email</span>
        <input
          type="email"
          autoComplete="email"
          className={inputClass}
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
        />
      </label>

      <label className={fieldClass}>
        <span className={labelClass}>Password</span>
        <input
          type="password"
          autoComplete="current-password"
          className={inputClass}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
      </label>

      <button type="submit" className={btnPrimaryClass} disabled={loading}>
        {loading ? 'Signing in…' : 'Login'}
      </button>

      {status && (
        <div
          role={isError ? 'alert' : 'status'}
          className={`rounded-md border px-3 py-2.5 text-sm leading-relaxed ${
            isError
              ? 'border-red-200 bg-red-50 text-red-800'
              : 'border-green-200 bg-green-50 text-green-800'
          }`}
        >
          {status}
        </div>
      )}

      <p className="m-0 text-center text-sm text-neutral-600">
        No account?{' '}
        <Link to="/signup" className="font-medium text-neutral-900 underline hover:text-neutral-700">
          Sign up
        </Link>
      </p>
      </form>
    </>
  )
}

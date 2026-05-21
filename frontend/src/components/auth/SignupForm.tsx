import { useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { getRtkErrorMessage } from '../../lib/apiError'
import {
  useConfirmSignupMutation,
  useSignupMutation,
} from '../../store/api/authApi'
import {
  btnPrimaryClass,
  fieldClass,
  formClass,
  inputClass,
  labelClass,
  statusClass,
} from '../../lib/styles'

export function SignupForm() {
  const navigate = useNavigate()
  const [signup, { isLoading: signupLoading }] = useSignupMutation()
  const [confirmSignup, { isLoading: confirmLoading }] = useConfirmSignupMutation()

  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [status, setStatus] = useState('')
  const [showVerify, setShowVerify] = useState(false)
  const [code, setCode] = useState('')
  const [verifyStatus, setVerifyStatus] = useState('')

  async function handleSignup(event: FormEvent) {
    event.preventDefault()
    setStatus('')
    setShowVerify(false)
    setVerifyStatus('')

    try {
      await signup({
        name: name.trim(),
        email: email.trim(),
        password,
        role: 'underwriter',
      }).unwrap()
      setStatus(
        'Signup successful. Check your email for a verification code, then verify below.',
      )
      setShowVerify(true)
    } catch (error) {
      setStatus(`Signup failed: ${getRtkErrorMessage(error)}`)
    }
  }

  async function handleVerify(event: FormEvent) {
    event.preventDefault()
    if (!email.trim() || !code.trim()) {
      setVerifyStatus('Email and verification code are required.')
      return
    }

    setVerifyStatus('')

    try {
      await confirmSignup({
        email: email.trim(),
        confirmation_code: code.trim(),
      }).unwrap()
      navigate('/login', {
        replace: true,
        state: { status: 'Email verified successfully. You can now log in.' },
      })
    } catch (error) {
      setVerifyStatus(`Verification failed: ${getRtkErrorMessage(error)}`)
    }
  }

  const signupIsError = status.startsWith('Signup failed')
  const verifyIsError =
    verifyStatus.startsWith('Verification failed') ||
    verifyStatus === 'Email and verification code are required.'

  return (
    <div className="space-y-0">
      <form className={formClass} onSubmit={handleSignup}>
        <label className={fieldClass}>
          <span className={labelClass}>Name</span>
          <input
            type="text"
            autoComplete="name"
            className={inputClass}
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
        </label>

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
            autoComplete="new-password"
            className={inputClass}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </label>

        <button type="submit" className={btnPrimaryClass} disabled={signupLoading}>
          {signupLoading ? 'Creating account...' : 'Create account'}
        </button>

        {status && <p className={statusClass(signupIsError)}>{status}</p>}

        <p className="m-0 text-center text-sm text-neutral-600">
          Already have an account?{' '}
          <Link to="/login" className="font-medium text-neutral-900 underline hover:text-neutral-700">
            Log in
          </Link>
        </p>
      </form>

      {showVerify && (
        <section className="mt-5 border-t border-neutral-200 pt-5">
          <h3 className="mb-1.5 text-[0.95rem] font-semibold text-neutral-900">
            Verify your email
          </h3>
          <p className="mb-4 text-sm text-neutral-600">
            Please enter the code sent to your inbox.
          </p>

          <form className={formClass} onSubmit={handleVerify}>
            <label className={fieldClass}>
              <span className={labelClass}>Verification code</span>
              <input
                type="text"
                inputMode="numeric"
                autoComplete="one-time-code"
                placeholder="123456"
                className={inputClass}
                value={code}
                onChange={(e) => setCode(e.target.value)}
                required
              />
            </label>

            <button type="submit" className={btnPrimaryClass} disabled={confirmLoading}>
              {confirmLoading ? 'Verifying...' : 'Verify & Confirm'}
            </button>

            {verifyStatus && <p className={statusClass(verifyIsError)}>{verifyStatus}</p>}
          </form>
        </section>
      )}
    </div>
  )
}

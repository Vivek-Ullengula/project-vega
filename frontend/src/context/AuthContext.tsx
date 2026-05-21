import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import { useDispatch } from 'react-redux'
import { getRtkErrorMessage } from '../lib/apiError'
import {
  AUTH_SESSION_EXPIRED_EVENT,
  loadStoredUser,
  persistUser,
} from '../lib/authSession'
import { baseApi } from '../store/api/baseApi'
import { useLoginMutation } from '../store/api/authApi'
import type { AppDispatch } from '../store/store'
import {
  emptySessionUser,
  welcomeMessage,
  type SessionUser,
} from '../types/auth'

interface AuthContextValue {
  user: SessionUser
  welcome: string
  login: (email: string, password: string) => Promise<string>
  logout: () => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const dispatch = useDispatch<AppDispatch>()
  const [loginMutation] = useLoginMutation()
  const [user, setUser] = useState<SessionUser>(loadStoredUser)
  const [welcome, setWelcome] = useState(() =>
    user.authenticated ? welcomeMessage(user) : '',
  )

  const login = useCallback(
    async (email: string, password: string) => {
      const trimmedEmail = email.trim()
      if (!trimmedEmail || !password) {
        return 'Please enter both email and password.'
      }

      try {
        const payload = await loginMutation({
          email: trimmedEmail,
          password,
        }).unwrap()

        const sessionUser: SessionUser = {
          authenticated: true,
          name: payload.user.name ?? '',
          email: payload.user.email ?? trimmedEmail,
          role: payload.user.role ?? '',
          token: payload.access_token,
        }

        const message = welcomeMessage(sessionUser)
        persistUser(sessionUser)
        setUser(sessionUser)
        setWelcome(message)
        dispatch(baseApi.util.resetApiState())
        return message
      } catch (error) {
        return `Login failed: ${getRtkErrorMessage(error)}`
      }
    },
    [dispatch, loginMutation],
  )

  const logout = useCallback(() => {
    persistUser(emptySessionUser())
    setUser(emptySessionUser())
    setWelcome('')
    dispatch(baseApi.util.resetApiState())
  }, [dispatch])

  useEffect(() => {
    window.addEventListener(AUTH_SESSION_EXPIRED_EVENT, logout)
    return () => window.removeEventListener(AUTH_SESSION_EXPIRED_EVENT, logout)
  }, [logout])

  const value = useMemo(
    () => ({ user, welcome, login, logout }),
    [user, welcome, login, logout],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) {
    throw new Error('useAuth must be used within AuthProvider')
  }
  return ctx
}

import { createContext } from 'react'
import type { SessionUser } from '../types/auth'

export interface AuthContextValue {
  user: SessionUser
  welcome: string
  login: (email: string, password: string) => Promise<string>
  logout: () => void
}

export const AuthContext = createContext<AuthContextValue | null>(null)

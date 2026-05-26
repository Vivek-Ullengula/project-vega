import { Navigate } from 'react-router-dom'
import { useAuth } from '../../context/useAuth'

export function GuestRoute({ children }: { children: React.ReactNode }) {
  const { user } = useAuth()

  if (user.authenticated) {
    return <Navigate to="/chat" replace />
  }

  return children
}

import { Navigate, useLocation } from 'react-router-dom'
import { useAuth } from '../../context/AuthContext'

export function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { user } = useAuth()
  const location = useLocation()

  if (!user.authenticated) {
    return <Navigate to="/login" state={{ from: location }} replace />
  }

  return children
}

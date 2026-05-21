import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { AuthLayout } from '../components/auth/AuthLayout'
import { GuestRoute } from '../components/auth/GuestRoute'
import { ProtectedRoute } from '../components/auth/ProtectedRoute'
import { HomePage } from '../pages/HomePage'
import { LoginPage } from '../pages/LoginPage'
import { SignupPage } from '../pages/SignupPage'

export function AppRouter() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Navigate to="/chat" replace />} />

        <Route
          path="/chat/:sessionId?"
          element={
            <ProtectedRoute>
              <HomePage />
            </ProtectedRoute>
          }
        />

        <Route
          element={
            <GuestRoute>
              <AuthLayout />
            </GuestRoute>
          }
        >
          <Route path="/login" element={<LoginPage />} />
          <Route path="/signup" element={<SignupPage />} />
        </Route>

        <Route path="*" element={<Navigate to="/chat" replace />} />
      </Routes>
    </BrowserRouter>
  )
}

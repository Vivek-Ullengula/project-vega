import { NavLink, Outlet } from 'react-router-dom'
import { tabClass } from '../../lib/styles'

function authTabClass({ isActive }: { isActive: boolean }) {
  return tabClass(isActive)
}

export function AuthLayout() {
  return (
    <main className="flex min-h-svh items-center justify-center bg-neutral-50 px-4 py-8 text-left">
      <div className="w-full max-w-md rounded-lg border border-neutral-200 bg-white p-6 shadow-none">
        <header>
          <h1 className="mb-1 text-lg font-semibold text-neutral-900">
            Coaction Binding Authority Assistant
          </h1>
        </header>

        <nav
          className="mb-5 flex border-b border-neutral-200"
          aria-label="Authentication"
        >
          <NavLink to="/login" className={authTabClass} end>
            Login
          </NavLink>
          <NavLink to="/signup" className={authTabClass} end>
            Signup
          </NavLink>
        </nav>

        <Outlet />
      </div>
    </main>
  )
}

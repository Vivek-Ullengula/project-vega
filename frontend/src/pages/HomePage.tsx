import { useNavigate } from 'react-router-dom'

import { ChatWorkspace } from '../components/chat/ChatWorkspace'

import { useAuth } from '../context/AuthContext'

import { btnSecondaryClass } from '../lib/styles'



export function HomePage() {

  const { welcome, logout } = useAuth()

  const navigate = useNavigate()



  function handleLogout() {

    logout()

    navigate('/login', { replace: true })

  }



  return (
    <main className="flex h-svh flex-col overflow-hidden bg-neutral-50 text-left">
      <header className="flex shrink-0 items-center justify-between gap-4 border-b border-neutral-200 bg-white px-6 py-4">
        <div className="flex min-w-0 items-center gap-3">
          <img
            src="/coaction.png"
            alt="Coaction"
            className="h-8 w-8 shrink-0 object-contain"
          />
          <p className="m-0 truncate text-base font-semibold text-neutral-900">
            {welcome}
          </p>
        </div>
        <button type="button" className={btnSecondaryClass} onClick={handleLogout}>
          Logout
        </button>
      </header>
      <ChatWorkspace />
    </main>

  )

}


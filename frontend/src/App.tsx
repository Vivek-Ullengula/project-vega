import { useEffect } from 'react'
import { AuthProvider } from './context/AuthContext'
import { captureAgentIdOverride } from './lib/chat'
import { AppRouter } from './routes/AppRouter'

function App() {
  useEffect(() => {
    captureAgentIdOverride()
  }, [])

  return (
    <AuthProvider>
      <AppRouter />
    </AuthProvider>
  )
}

export default App

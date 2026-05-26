import { useCallback, useEffect, useRef, useState, type Dispatch, type SetStateAction } from 'react'

import { useNavigate, useParams } from 'react-router-dom'

import type { ChatMessage as ChatMessageType } from '../../types/chat'

import type { StoredSessionMessage } from '../../types/session'

import { useGetSessionQuery } from '../../store/api/sessionApi'

import { uuid } from '../../lib/uuid'

import { ChatPanel } from './ChatPanel'

import { SessionSidebar } from './SessionSidebar'



function messageId(): string {

  return uuid()

}



function mapStoredToChatMessages(stored: StoredSessionMessage[] | null | undefined): ChatMessageType[] {

  const out: ChatMessageType[] = []

  if (!stored || !Array.isArray(stored)) return out

  for (const m of stored) {

    if (!m) continue

    const role = m.role === 'user' || m.role === 'assistant' ? m.role : null

    if (!role || typeof m.content !== 'string') continue

    out.push({ id: messageId(), role, content: m.content, citations: m.citations ?? [] })

  }

  return out

}



function chatPath(sessionId: string): string {

  return sessionId ? `/chat/${encodeURIComponent(sessionId)}` : '/chat'

}



export function ChatWorkspace() {

  const { sessionId: routeSessionId } = useParams<{ sessionId?: string }>()

  const sessionId = routeSessionId ?? ''

  const navigate = useNavigate()



  const [sessionSidebarOpen, setSessionSidebarOpen] = useState(true)

  const [messages, setMessages] = useState<ChatMessageType[]>([])

  const [followUps, setFollowUps] = useState<string[]>([])

  /** When true, URL has a session but messages are managed locally (new message flow). */

  const [skipServerSync, setSkipServerSync] = useState(false)

  const locallyGeneratedSessionIdRef = useRef<string | null>(null)



  const { data: sessionDetail, isError: sessionLoadError } = useGetSessionQuery(sessionId, {

    skip: !sessionId || skipServerSync,

    refetchOnMountOrArgChange: true,

  })



  const setSessionId = useCallback<Dispatch<SetStateAction<string>>>(

    (id) => {

      const next = typeof id === 'function' ? id(sessionId) : id

      if (next === sessionId) return

      setSkipServerSync(true)

      locallyGeneratedSessionIdRef.current = next

      navigate(chatPath(next), { replace: !sessionId })

    },

    [navigate, sessionId],

  )



  const handleNewChat = useCallback(() => {

    setMessages([])

    setFollowUps([])

    navigate('/chat')

  }, [navigate])



  const handleSelectSession = useCallback(

    (id: string) => {

      setSkipServerSync(false)

      navigate(chatPath(id))

    },

    [navigate],

  )



  useEffect(() => {

    let cancelled = false

    const updateState = (fn: () => void) => {
      queueMicrotask(() => {
        if (!cancelled) fn()
      })
    }

    if (sessionId) {

      if (sessionId === locallyGeneratedSessionIdRef.current) {

        // Keep skipServerSync as true since it's locally managed

      } else {

        updateState(() => {
          setSkipServerSync(false)
          locallyGeneratedSessionIdRef.current = null
        })

      }

    } else {

      updateState(() => {
        setMessages([])
        setFollowUps([])
        setSkipServerSync(false)
        locallyGeneratedSessionIdRef.current = null
      })

    }

    return () => {
      cancelled = true
    }

  }, [sessionId])



  useEffect(() => {

    if (!sessionId || skipServerSync || !sessionDetail) return

    let cancelled = false
    const nextMessages = mapStoredToChatMessages(sessionDetail.messages)

    queueMicrotask(() => {
      if (cancelled) return
      setMessages(nextMessages)
      setFollowUps([])
    })

    return () => {
      cancelled = true
    }

  }, [sessionId, sessionDetail, skipServerSync])



  useEffect(() => {

    if (!sessionId || skipServerSync || !sessionLoadError) return

    navigate('/chat', { replace: true })

  }, [sessionId, sessionLoadError, skipServerSync, navigate])



  return (

    <div className="flex min-h-0 flex-1 overflow-hidden">

      {sessionSidebarOpen ? (

        <SessionSidebar

          currentSessionId={sessionId}

          onClose={() => setSessionSidebarOpen(false)}

          onNewChat={handleNewChat}

          onSelectSession={handleSelectSession}

        />

      ) : (

        <button

          type="button"

          className="flex w-10 shrink-0 flex-col items-center border-r border-neutral-200 bg-white py-4 text-lg leading-none text-neutral-500 hover:bg-neutral-100 hover:text-neutral-900"

          title="Show sessions"

          aria-label="Show sessions"

          onClick={() => setSessionSidebarOpen(true)}

        >

          ›

        </button>

      )}

      <ChatPanel

        sessionId={sessionId}

        setSessionId={setSessionId}

        messages={messages}

        setMessages={setMessages}

        followUps={followUps}

        setFollowUps={setFollowUps}

        onNewChat={handleNewChat}

      />

    </div>

  )

}

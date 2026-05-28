import { useCallback, useEffect, useRef, useState, type Dispatch, type SetStateAction } from 'react'
import { ChatMessage, CoactionAssistantAvatar } from './ChatMessage'
import { ChatInput } from './ChatInput'
import { getRtkErrorMessage } from '../../lib/apiError'
import { buildAssistantContent, newSessionId, streamAgentResponse } from '../../lib/chat'
import { btnSecondaryClass } from '../../lib/styles'
import { uuid } from '../../lib/uuid'
import { baseApi } from '../../store/api/baseApi'
import { useAppDispatch } from '../../store/hooks'
import type { ChatMessage as ChatMessageType } from '../../types/chat'

function messageId(): string {
  return uuid()
}

const EMPTY_PLACEHOLDER = (
  <div className="flex flex-col items-center justify-center px-6 py-16 text-center text-neutral-500">
    <p className="m-0 text-base font-semibold text-neutral-900">
      Coaction Binding Authority Assistant
    </p>
    <p className="mt-2 max-w-md text-sm text-neutral-600">
      Ask about class codes, coverage options, or manual guidelines.
    </p>
  </div>
)

function LoadingDots() {
  return (
    <div className="flex justify-start gap-2">
      <CoactionAssistantAvatar />
      <div
        className="flex items-center rounded-lg border border-[#E5E5E5] bg-[#F9F9F9] px-4 py-3"
        role="status"
        aria-live="polite"
        aria-label="Generating response"
      >
        <span className="flex items-center gap-1" aria-hidden="true">
          <span className="size-1.5 animate-bounce rounded-full bg-[#6B7280] [animation-delay:-0.2s]" />
          <span className="size-1.5 animate-bounce rounded-full bg-[#6B7280] [animation-delay:-0.1s]" />
          <span className="size-1.5 animate-bounce rounded-full bg-[#6B7280]" />
        </span>
      </div>
    </div>
  )
}

export type ChatPanelProps = {
  sessionId: string
  setSessionId: Dispatch<SetStateAction<string>>
  messages: ChatMessageType[]
  setMessages: Dispatch<SetStateAction<ChatMessageType[]>>
  followUps: string[]
  setFollowUps: Dispatch<SetStateAction<string[]>>
  onNewChat: () => void
}

export function ChatPanel({
  sessionId,
  setSessionId,
  messages,
  setMessages,
  followUps,
  setFollowUps,
  onNewChat,
}: ChatPanelProps) {
  const dispatch = useAppDispatch()
  const [isLoading, setIsLoading] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages, isLoading])

  const sendMessage = useCallback(
    async (text: string) => {
      const activeSession = sessionId || newSessionId()
      if (!sessionId) setSessionId(activeSession)

      const userMsg: ChatMessageType = {
        id: messageId(),
        role: 'user',
        content: text,
      }
      setMessages((prev) => [...prev, userMsg])
      setFollowUps([])
      setIsLoading(true)

      const assistantId = messageId()
      let assistantStarted = false
      let streamedContent = ''

      try {
        await streamAgentResponse(
          {
            input_text: text,
            session_id: activeSession,
            top_k: 5,
          },
          {
            onSession: (streamSessionId) => {
              if (streamSessionId) setSessionId(streamSessionId)
            },
            onDelta: (delta) => {
              streamedContent = `${streamedContent}${delta}`
              if (!assistantStarted) {
                assistantStarted = true
                setMessages((prev) => [
                  ...prev,
                  {
                    id: assistantId,
                    role: 'assistant',
                    content: delta,
                    citations: [],
                  },
                ])
                return
              }

              setMessages((prev) =>
                prev.map((msg) =>
                  msg.id === assistantId ? { ...msg, content: `${msg.content}${delta}` } : msg,
                ),
              )
            },
            onFinal: (data) => {
              if (data.session_id) setSessionId(data.session_id)
              const finalContent = buildAssistantContent(data).trim() || streamedContent

              const assistantMsg: ChatMessageType = {
                id: assistantId,
                role: 'assistant',
                content: finalContent,
                citations: data.citations ?? [],
              }

              setMessages((prev) => {
                if (assistantStarted) {
                  return prev.map((msg) => (msg.id === assistantId ? assistantMsg : msg))
                }
                assistantStarted = true
                return [...prev, assistantMsg]
              })
              setFollowUps(data.metadata?.follow_up_questions?.filter(Boolean) ?? [])
              dispatch(baseApi.util.invalidateTags([{ type: 'Session', id: 'LIST' }]))
            },
          },
        )
      } catch (error) {
        const assistantMsg: ChatMessageType = {
          id: messageId(),
          role: 'assistant',
          content: `Warning: ${getRtkErrorMessage(error)}`,
        }
        setMessages((prev) => [...prev, assistantMsg])
      } finally {
        setIsLoading(false)
      }
    },
    [dispatch, sessionId, setSessionId, setMessages, setFollowUps],
  )

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col bg-neutral-50">
      <div className="flex shrink-0 items-center border-b border-neutral-200 bg-white px-4 py-2.5 shadow-sm">
        <span className="text-xs font-semibold uppercase tracking-wide text-neutral-500">
          Underwriting Assistant
        </span>
      </div>
      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
        <div className="flex flex-col gap-4">
          {messages.length === 0 && !isLoading ? (
            EMPTY_PLACEHOLDER
          ) : (
            <>
              {messages.map((msg) => (
                <ChatMessage key={msg.id} message={msg} />
              ))}
              {isLoading ? <LoadingDots /> : null}
            </>
          )}
        </div>
      </div>

      {followUps.length > 0 ? (
        <div className="flex shrink-0 flex-wrap gap-2 border-t border-neutral-100 bg-neutral-50 px-4 py-2">
          {followUps.map((q) => (
            <button
              key={q}
              type="button"
              className={btnSecondaryClass}
              disabled={isLoading}
              onClick={() => void sendMessage(q)}
            >
              {q}
            </button>
          ))}
        </div>
      ) : null}

      <ChatInput
        clearDisabled={messages.length === 0 && followUps.length === 0 && !sessionId}
        disabled={isLoading}
        onClear={onNewChat}
        onSend={(text) => void sendMessage(text)}
      />
    </div>
  )
}

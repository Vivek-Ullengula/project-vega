import { Trash2 } from 'lucide-react'

import { btnSecondaryClass } from '../../lib/styles'
import {
  useDeleteSessionMutation,
  useListSessionsQuery,
} from '../../store/api/sessionApi'

function truncateTitle(title: string, max = 36): string {
  const t = title.trim() || 'New Chat'
  return t.length <= max ? t : `${t.slice(0, max - 1)}…`
}

function formatLastAccessed(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''

  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date)
}

function getTime(value: string): number {
  const time = new Date(value).getTime()
  return Number.isNaN(time) ? 0 : time
}

type SessionSidebarProps = {
  currentSessionId: string
  onSelectSession: (sessionId: string) => void
  onNewChat: () => void
  onClose: () => void
}

export function SessionSidebar({
  currentSessionId,
  onSelectSession,
  onNewChat,
  onClose,
}: SessionSidebarProps) {
  const { data: sessions = [], isLoading, isError, refetch } = useListSessionsQuery()
  const [deleteSession, { isLoading: isDeleting }] = useDeleteSessionMutation()
  const sortedSessions = [...sessions].sort(
    (a, b) => getTime(b.last_accessed) - getTime(a.last_accessed),
  )

  return (
    <aside className="flex w-[min(100%,280px)] shrink-0 flex-col border-r border-neutral-200 bg-white">
      <div className="flex shrink-0 items-stretch gap-2 border-b border-neutral-200 p-3">
        <button type="button" className={`${btnSecondaryClass} min-w-0 flex-1 text-base font-semibold text-neutral-900`} onClick={onNewChat}>
          New chat
        </button>
        <button
          type="button"
          className="shrink-0 rounded-md border border-neutral-200 bg-white px-2.5 py-2 text-sm text-neutral-600 hover:bg-neutral-100 hover:text-neutral-900"
          title="Hide sessions"
          aria-label="Hide sessions"
          onClick={onClose}
        >
          ‹
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-2">
        <p className="m-0 px-2 pb-2 text-xs font-semibold uppercase tracking-wide text-neutral-900">
          Chats
        </p>

        {isLoading ? (
          <p className="m-0 px-2 text-sm text-neutral-500">Loading…</p>
        ) : null}

        {isError ? (
          <div className="px-2">
            <p className="m-0 text-sm text-red-700">Could not load sessions.</p>
            <button type="button" className={`${btnSecondaryClass} mt-2 w-full`} onClick={() => void refetch()}>
              Retry
            </button>
          </div>
        ) : null}

        {!isLoading && !isError && sessions.length === 0 ? (
          <p className="m-0 px-2 text-sm text-neutral-600">No conversations yet.</p>
        ) : null}

        <ul className="m-0 list-none space-y-1 p-0">
          {sortedSessions.map((s) => {
            const active = s.session_id === currentSessionId
            return (
              <li key={s.session_id}>
                <div
                  className={`flex items-stretch overflow-hidden rounded-md border transition-colors ${
                    active
                      ? 'border-neutral-900 bg-neutral-900 text-white'
                      : 'border-neutral-200 bg-white text-neutral-900 hover:bg-neutral-50'
                  }`}
                >
                  <button
                    type="button"
                    className={`min-w-0 flex-1 px-2.5 py-2 text-left text-sm font-medium ${
                      active ? 'text-white' : 'text-neutral-900'
                    }`}
                    onClick={() => onSelectSession(s.session_id)}
                  >
                    <span className="block truncate">{truncateTitle(s.title)}</span>
                    <span
                      className={`mt-0.5 block truncate text-xs font-normal ${
                        active ? 'text-neutral-300' : 'text-neutral-500'
                      }`}
                    >
                      {formatLastAccessed(s.last_accessed)}
                    </span>
                  </button>
                  <button
                    type="button"
                    title="Delete chat"
                    aria-label={`Delete ${truncateTitle(s.title)}`}
                    disabled={isDeleting}
                    className={`flex shrink-0 items-center justify-center px-2 py-2 ${
                      active
                        ? 'border-neutral-700 text-neutral-300 hover:bg-neutral-800 hover:text-white'
                        : 'border-neutral-200 text-neutral-500 hover:bg-red-50 hover:text-red-800'
                    }`}
                    onClick={(e) => {
                      e.stopPropagation()
                      void deleteSession(s.session_id)
                        .unwrap()
                        .then(() => {
                          if (s.session_id === currentSessionId) onNewChat()
                        })
                        .catch(() => {
                          /* errors surfaced by RTK / optional toast */
                        })
                    }}
                  >
                    <Trash2 className="size-4" strokeWidth={2} aria-hidden />
                  </button>
                </div>
              </li>
            )
          })}
        </ul>
      </div>
    </aside>
  )
}

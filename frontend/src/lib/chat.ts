import type { AgentInvokeResponse } from '../types/chat'
import { getApiBaseUrl, parseApiErrorBody } from './apiError'
import { AUTH_STORAGE_KEY } from './authSession'
import { uuid } from './uuid'

export const DEFAULT_AGENT_ID =
  (import.meta.env.VITE_AGENT_ID ?? 'coaction-underwriting').trim() ||
  'coaction-underwriting'

export function newSessionId(): string {
  return uuid()
}

export function buildAssistantContent(data: AgentInvokeResponse): string {
  if (data.status === 'error') {
    return `Warning: ${data.answer}`
  }
  return data.answer
}

type StreamAgentCallbacks = {
  onSession?: (sessionId: string) => void
  onDelta?: (text: string) => void
  onFinal?: (response: AgentInvokeResponse) => void
}

function authHeaders(): HeadersInit {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    'X-Request-Time': new Date().toISOString(),
  }

  try {
    const raw = localStorage.getItem(AUTH_STORAGE_KEY)
    if (!raw) return headers
    const user = JSON.parse(raw) as { token?: string }
    if (!user?.token) return headers
    const authVal = `Bearer ${user.token}`
    headers.Authorization = authVal
    headers['X-Amzn-Bedrock-AgentCore-Runtime-Custom-Authorization'] = authVal
  } catch {
    /* ignore corrupt storage */
  }

  return headers
}

function parseSseBlock(block: string): unknown {
  const data = block
    .split('\n')
    .filter((line) => line.startsWith('data:'))
    .map((line) => line.slice(5).trimStart())
    .join('\n')

  if (!data) return null
  return JSON.parse(data)
}

export async function streamAgentResponse(
  body: {
    input_text: string
    session_id?: string | null
    top_k?: number
  },
  callbacks: StreamAgentCallbacks,
): Promise<void> {
  const response = await fetch(`${getApiBaseUrl()}/agents/${DEFAULT_AGENT_ID}/invoke/stream`, {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify(body),
  })

  if (!response.ok) {
    let errorBody: unknown = await response.text()
    try {
      errorBody = JSON.parse(String(errorBody))
    } catch {
      /* keep text */
    }
    throw new Error(parseApiErrorBody(errorBody, response.status, response.statusText))
  }

  if (!response.body) {
    throw new Error('Streaming response was empty.')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { value, done } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })
    let separatorIndex = buffer.indexOf('\n\n')

    while (separatorIndex >= 0) {
      const block = buffer.slice(0, separatorIndex).trim()
      buffer = buffer.slice(separatorIndex + 2)
      separatorIndex = buffer.indexOf('\n\n')

      if (!block) continue
      const parsed = parseSseBlock(block)
      if (!parsed || typeof parsed !== 'object') continue

      const event = parsed as {
        type?: string
        session_id?: string
        text?: string
        response?: AgentInvokeResponse
      }

      if (event.type === 'session' && event.session_id) {
        callbacks.onSession?.(event.session_id)
      } else if (event.type === 'delta' && event.text) {
        callbacks.onDelta?.(event.text)
      } else if (event.type === 'final' && event.response) {
        callbacks.onFinal?.(event.response)
      }
    }
  }
}

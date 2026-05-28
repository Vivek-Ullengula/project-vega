import type { AgentInvokeResponse } from '../types/chat'
import { getApiBaseUrl, parseApiErrorBody } from './apiError'
import { AUTH_STORAGE_KEY } from './authSession'
import { uuid } from './uuid'

export const DEFAULT_AGENT_ID =
  (import.meta.env.VITE_AGENT_ID ?? 'coaction-underwriting').trim() ||
  'coaction-underwriting'
const AGENT_ID_OVERRIDE_KEY = 'vega_agent_id_override'

export function captureAgentIdOverride(): void {
  if (typeof window === 'undefined') return
  const agentId = new URLSearchParams(window.location.search).get('agent_id')?.trim()
  if (agentId) {
    window.localStorage.setItem(AGENT_ID_OVERRIDE_KEY, agentId)
    return
  }
  window.localStorage.removeItem(AGENT_ID_OVERRIDE_KEY)
}

export function getAgentId(): string {
  if (typeof window === 'undefined') return DEFAULT_AGENT_ID
  return window.localStorage.getItem(AGENT_ID_OVERRIDE_KEY)?.trim() || DEFAULT_AGENT_ID
}

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

function findSseSeparator(value: string): { index: number; length: number } | null {
  const match = /\r?\n\r?\n/.exec(value)
  if (!match) return null
  return { index: match.index, length: match[0].length }
}

export async function streamAgentResponse(
  body: {
    input_text: string
    session_id?: string | null
    top_k?: number
  },
  callbacks: StreamAgentCallbacks,
): Promise<void> {
  const response = await fetch(`${getApiBaseUrl()}/agents/${getAgentId()}/invoke/stream`, {
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
  let finalReceived = false

  const handleBlock = (rawBlock: string) => {
    const block = rawBlock.trim()
    if (!block) return
    const parsed = parseSseBlock(block)
    if (!parsed || typeof parsed !== 'object') return

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
      finalReceived = true
      callbacks.onFinal?.(event.response)
    }
  }

  const flushCompleteBlocks = () => {
    let separator = findSseSeparator(buffer)

    while (separator) {
      const block = buffer.slice(0, separator.index)
      buffer = buffer.slice(separator.index + separator.length)
      handleBlock(block)
      separator = findSseSeparator(buffer)
    }
  }

  while (true) {
    const { value, done } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })
    flushCompleteBlocks()
  }

  buffer += decoder.decode()
  flushCompleteBlocks()

  if (buffer.trim()) {
    handleBlock(buffer)
  }

  if (!finalReceived) {
    throw new Error('Streaming response ended before a final answer was received.')
  }
}

export type ChatRole = 'user' | 'assistant'

export interface ChatMessage {
  id: string
  role: ChatRole
  content: string
}

export interface SourceCitation {
  source_id: string
  title?: string | null
  uri?: string | null
  manual_name?: string | null
  chunk_id?: string | null
  score?: number | null
}

export interface AgentInvokeRequest {
  input_text: string
  session_id?: string | null
  top_k?: number
}

export interface AgentInvokeResponse {
  status: 'success' | 'clarification_required' | 'blocked' | 'escalated' | 'error'
  answer: string
  citations?: SourceCitation[]
  session_id: string
  metadata?: {
    follow_up_questions?: string[]
    sources?: unknown[]
  }
}

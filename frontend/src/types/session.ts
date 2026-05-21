export interface SessionSummary {
  session_id: string
  title: string
  last_accessed: string
  message_count: number
}

/** Message shape persisted in DynamoDB / returned by GET /sessions/:id */
export interface StoredSessionMessage {
  role: string
  content: string
}

export interface SessionDetail {
  session_id: string
  title: string
  messages: StoredSessionMessage[]
  last_accessed: string
}

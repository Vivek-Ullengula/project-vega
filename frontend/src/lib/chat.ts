import type { AgentInvokeResponse, SourceCitation } from '../types/chat'

export const DEFAULT_AGENT_ID =
  (import.meta.env.VITE_AGENT_ID ?? 'coaction-underwriting').trim() ||
  'coaction-underwriting'

export function newSessionId(): string {
  return crypto.randomUUID()
}

export function formatAnswerWithCitations(
  answer: string,
  citations: SourceCitation[] | undefined,
): string {
  if (!citations?.length) return answer

  let text = answer
  text += '\n\nSources:\n'
  for (const c of citations) {
    const manual = c.manual_name ?? 'Binding Authority Manual'
    const title = c.title ?? c.source_id ?? 'Source'
    const uri = c.uri ?? '#'
    text += `\nSource Manual: ${manual}\nSection: ${title}\nLink: ${uri}\n`
  }
  return text
}

export function buildAssistantContent(data: AgentInvokeResponse): string {
  let answer = data.answer
  if (data.status === 'error') {
    answer = `⚠️ ${answer}`
  }
  return formatAnswerWithCitations(answer, data.citations)
}

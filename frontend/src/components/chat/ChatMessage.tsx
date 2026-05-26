import { useState, useCallback } from 'react'

import { Copy, Check, ExternalLink } from 'lucide-react'

import type { ChatMessage as ChatMessageType, SourceCitation } from '../../types/chat'

import { MarkdownContent } from '../markdown'

interface ChatMessageProps {
  message: ChatMessageType
}

const COACTION_AVATAR = '/coaction.png'

export function CoactionAssistantAvatar({ className = '' }: { className?: string }) {
  return (
    <img
      src={COACTION_AVATAR}
      alt=""
      className={[
        'mt-1 size-8 shrink-0 rounded-full border border-[#E5E5E5] bg-white object-cover',
        className,
      ]
        .filter(Boolean)
        .join(' ')}
      width={32}
      height={32}
    />
  )
}

function stripThinking(content: string): string {
  const re = /<thinking>([\s\S]*?)<\/thinking>/gi
  return content.replace(re, '').trim()
}

function copyIconButtonClass() {
  return [
    'inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md',
    'text-neutral-500 transition-colors hover:bg-neutral-200/80 hover:text-neutral-700',
    'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-neutral-400',
  ].join(' ')
}

function SourceList({ citations }: { citations?: SourceCitation[] }) {
  const visibleCitations =
    citations?.filter((citation) => citation.source_id || citation.title || citation.uri).slice(0, 3) ??
    []

  if (!visibleCitations.length) return null

  return (
    <div className="mt-3 border-t border-[#E5E5E5] pt-3">
      <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-[#6B7280]">
        Citations -
      </p>
      <ol className="space-y-3">
        {visibleCitations.map((citation, index) => {
          const sourceId = citation.source_id || `S${index + 1}`
          const title = citation.title || sourceId
          const manual = citation.manual_name || 'Binding Authority Manual'
          const titleCode = title.match(/\bClass Code\s+(\d{4,})\b/i)?.[1]
          const urlCode = citation.uri?.match(/\/(\d{4,})\.html(?:$|[?#])/i)?.[1]
          const classCode = citation.class_code || titleCode || urlCode || 'N/A'

          return (
            <li key={`${sourceId}-${index}`} className="text-xs leading-relaxed text-[#4B5563]">
              <p>
                <span className="font-semibold text-[#374151]">Source Manual:</span> {manual}
              </p>
              <p>
                <span className="font-semibold text-[#374151]">Class Code:</span> {classCode}
              </p>
              <p className="min-w-0">
                <span className="font-semibold text-[#374151]">Link:</span>{' '}
                {citation.uri ? (
                  <a
                    href={citation.uri}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex max-w-full items-center gap-1 font-medium text-blue-700 underline decoration-blue-700/50 underline-offset-2 hover:text-blue-900"
                  >
                    <span className="truncate">{title}</span>
                    <ExternalLink className="size-3 shrink-0" strokeWidth={2} />
                  </a>
                ) : (
                  <span className="font-medium text-[#374151]">N/A</span>
                )}
              </p>
            </li>
          )
        })}
      </ol>
    </div>
  )
}

export function ChatMessage({ message }: ChatMessageProps) {
  const isUser = message.role === 'user'
  const body = stripThinking(message.content)
  const markdownSource = body || message.content
  const [copied, setCopied] = useState(false)

  const copyPlainText = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(message.content)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // Clipboard may be unavailable in restricted browser contexts.
    }
  }, [message.content])

  const copyButton = (
    <button
      type="button"
      className={copyIconButtonClass()}
      aria-label="Copy message"
      onClick={() => void copyPlainText()}
    >
      {copied ? (
        <Check className="size-3.5 text-green-600 animate-in fade-in zoom-in-75 duration-200" strokeWidth={2} />
      ) : (
        <Copy className="size-3.5" strokeWidth={2} />
      )}
    </button>
  )

  if (isUser) {
    return (
      <div className="flex justify-end">
        <div className="flex max-w-[85%] flex-col items-end gap-1">
          <div
            className={[
              'rounded-lg border px-4 py-3 text-sm leading-relaxed',
              'border-[#FDE6D2] bg-[#FFF5EB] text-[#374151]',
            ].join(' ')}
          >
            <MarkdownContent markdown={markdownSource} variant="onLight" />
          </div>
          <div className="flex justify-end">{copyButton}</div>
        </div>
      </div>
    )
  }

  return (
    <div className="flex justify-start gap-2">
      <CoactionAssistantAvatar />
      <div className="relative max-w-[min(85%,calc(100%-2.5rem))]">
        <div
          className={[
            'rounded-lg border px-4 py-3 pr-9 text-sm leading-relaxed',
            'border-[#E5E5E5] bg-[#F9F9F9] text-[#374151]',
          ].join(' ')}
        >
          <div className="absolute right-2 top-2">{copyButton}</div>
          <MarkdownContent markdown={markdownSource} variant="onLight" />
          <SourceList citations={message.citations} />
        </div>
      </div>
    </div>
  )
}

import { useCallback } from 'react'

import { Copy } from 'lucide-react'

import type { ChatMessage as ChatMessageType } from '../../types/chat'

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

      className={['mt-1 size-8 shrink-0 rounded-full border border-[#E5E5E5] bg-white object-cover', className].filter(Boolean).join(' ')}

      width={32}

      height={32}

    />

  )

}



function splitThinking(content: string): { body: string; thinking: string | null } {

  const re = /<thinking>([\s\S]*?)<\/thinking>/gi

  const thinkingParts: string[] = []

  let m: RegExpExecArray | null

  while ((m = re.exec(content)) !== null) {

    const inner = m[1]?.trim()

    if (inner) thinkingParts.push(inner)

  }

  const body = content.replace(re, '').trim()

  const thinking = thinkingParts.length > 0 ? thinkingParts.join('\n\n') : null

  return { body, thinking }

}



function copyIconButtonClass() {

  return [

    'inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md',

    'text-neutral-500 transition-colors hover:bg-neutral-200/80 hover:text-neutral-700',

    'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-neutral-400',

  ].join(' ')

}



export function ChatMessage({ message }: ChatMessageProps) {

  const isUser = message.role === 'user'

  const { body, thinking } = splitThinking(message.content)

  const markdownSource = body || message.content



  const copyPlainText = useCallback(async () => {

    try {

      await navigator.clipboard.writeText(message.content)

    } catch {

      // ignore — clipboard may be denied

    }

  }, [message.content])



  const CopyBtn = (

    <button

      type="button"

      className={copyIconButtonClass()}

      aria-label="Copy message"

      onClick={() => void copyPlainText()}

    >

      <Copy className="size-3.5" strokeWidth={2} />

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

          <div className="flex justify-end">{CopyBtn}</div>

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

          <div className="absolute right-2 top-2">{CopyBtn}</div>

          <MarkdownContent markdown={markdownSource} variant="onLight" />

          {thinking ? (

            <p className="mt-3 border-t border-[#E5E5E5] pt-3 text-sm leading-relaxed text-[#6B7280]">

              &lt;thinking&gt; {thinking} &lt;/thinking&gt;

            </p>

          ) : null}

        </div>

      </div>

    </div>

  )

}



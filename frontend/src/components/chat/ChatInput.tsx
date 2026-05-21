import { useState, type FormEvent, type KeyboardEvent } from 'react'
interface ChatInputProps {
  clearDisabled?: boolean
  disabled?: boolean
  onClear: () => void
  onSend: (text: string) => void
}

export function ChatInput({
  clearDisabled = false,
  disabled = false,
  onClear,
  onSend,
}: ChatInputProps) {
  const [text, setText] = useState('')

  function submit() {
    const trimmed = text.trim()
    if (!trimmed || disabled) return
    onSend(trimmed)
    setText('')
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault()
    submit()
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      submit()
    }
  }

  return (
    <form
      className="z-10 flex shrink-0 items-end gap-2 border-t border-neutral-200 bg-white px-4 py-3 shadow-[0_-4px_12px_rgba(0,0,0,0.06)]"
      onSubmit={handleSubmit}
    >
      <textarea
        className="min-h-[44px] max-h-32 flex-1 resize-none rounded-md border border-neutral-300 bg-white px-3 py-2.5 text-sm text-neutral-900 outline-none focus:ring-2 focus:ring-neutral-400 disabled:opacity-60"
        placeholder="Type your underwriting query…"
        rows={1}
        value={text}
        disabled={disabled}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={handleKeyDown}
      />
      <button
        type="button"
        className="mt-0 shrink-0 cursor-pointer rounded-md border border-neutral-300 bg-white px-4 py-2.5 text-sm font-medium text-neutral-700 transition-colors hover:bg-neutral-100 disabled:cursor-not-allowed disabled:opacity-60"
        disabled={disabled || clearDisabled}
        onClick={onClear}
      >
        Clear
      </button>
      <button
        type="submit"
        className="mt-0 shrink-0 cursor-pointer rounded-md bg-neutral-900 px-5 py-2.5 text-sm font-medium text-white transition-colors hover:bg-neutral-700 disabled:cursor-not-allowed disabled:opacity-60"
        disabled={disabled || !text.trim()}
      >
        Send
      </button>
    </form>
  )
}

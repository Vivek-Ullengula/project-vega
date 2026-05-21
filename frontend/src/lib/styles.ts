/** Shared Tailwind class strings — monochrome theme (Gradio parity) */

export const formClass = 'flex flex-col gap-3.5'

export const fieldClass = 'flex flex-col gap-1.5'

export const labelClass = 'text-xs font-medium text-neutral-600'

export const inputClass =
  'w-full rounded-md border border-neutral-300 bg-white px-3 py-2.5 text-sm text-neutral-900 outline-none focus:ring-2 focus:ring-neutral-400'

export const btnPrimaryClass =
  'mt-1 w-full cursor-pointer rounded-md bg-neutral-900 px-4 py-2.5 text-sm font-medium text-white transition-colors hover:bg-neutral-700 disabled:cursor-not-allowed disabled:opacity-60'

export const btnSecondaryClass =
  'cursor-pointer rounded-md border border-neutral-300 bg-white px-3.5 py-2 text-sm text-neutral-700 transition-colors hover:border-neutral-400 hover:bg-neutral-50'

export function statusClass(isError: boolean) {
  return `m-0 text-sm leading-relaxed ${isError ? 'text-red-700' : 'text-green-700'}`
}

export function tabClass(active: boolean) {
  return [
    'flex-1 cursor-pointer border-b-2 px-4 py-2.5 text-sm transition-colors -mb-px',
    active
      ? 'border-neutral-900 font-semibold text-neutral-900'
      : 'border-transparent text-neutral-600 hover:bg-neutral-100 hover:text-neutral-900',
  ].join(' ')
}

import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Components } from 'react-markdown'

export type MarkdownVariant = 'onLight' | 'onDark'

export type MarkdownContentProps = {
  /** Markdown source string */
  markdown: string
  /** Surface style: light card vs dark / inverted bubble */
  variant?: MarkdownVariant
  className?: string
}

function markdownComponents(variant: MarkdownVariant): Components {
  const onDark = variant === 'onDark'

  const linkClass = onDark
    ? 'font-medium text-sky-300 underline decoration-sky-300/60 underline-offset-2 hover:text-sky-200'
    : 'font-medium text-blue-700 underline decoration-blue-700/50 underline-offset-2 hover:text-blue-900'

  const inlineCodeClass = onDark
    ? 'rounded bg-white/20 px-1 py-0.5 font-mono text-[0.9em] text-white'
    : 'rounded bg-neutral-100 px-1 py-0.5 font-mono text-[0.9em] text-neutral-800'

  const blockPreClass = onDark
    ? 'mb-2 overflow-x-auto rounded-md border border-white/10 bg-black/25 p-3 text-xs leading-relaxed text-neutral-100 last:mb-0 [&>code]:bg-transparent [&>code]:p-0'
    : 'mb-2 overflow-x-auto rounded-md border border-neutral-200 bg-neutral-950 p-3 text-xs leading-relaxed text-neutral-100 last:mb-0 [&>code]:bg-transparent [&>code]:p-0'

  const tableShell = onDark
    ? 'mb-2 block max-w-full overflow-x-auto rounded-md border border-white/15 last:mb-0'
    : 'mb-2 block max-w-full overflow-x-auto rounded-md border border-neutral-200 last:mb-0'

  const thTd = onDark ? 'border border-white/15 px-2 py-1.5 align-top' : 'border border-neutral-200 px-2 py-1.5 align-top'

  return {
    p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
    ul: ({ children }) => (
      <ul className="mb-2 list-disc space-y-1 pl-5 last:mb-0 marker:text-current">{children}</ul>
    ),
    ol: ({ children }) => (
      <ol className="mb-2 list-decimal space-y-1 pl-5 last:mb-0 marker:font-medium">{children}</ol>
    ),
    li: ({ children }) => <li className="leading-relaxed [&>p]:mb-0">{children}</li>,
    h1: ({ children }) => (
      <h1 className="mb-2 border-b border-current/15 pb-1 text-base font-semibold tracking-tight last:mb-0">
        {children}
      </h1>
    ),
    h2: ({ children }) => <h2 className="mb-2 text-base font-semibold tracking-tight last:mb-0">{children}</h2>,
    h3: ({ children }) => <h3 className="mb-1.5 text-sm font-semibold last:mb-0">{children}</h3>,
    h4: ({ children }) => <h4 className="mb-1.5 text-sm font-semibold opacity-95 last:mb-0">{children}</h4>,
    h5: ({ children }) => <h5 className="mb-1 text-sm font-medium last:mb-0">{children}</h5>,
    h6: ({ children }) => <h6 className="mb-1 text-sm font-medium opacity-90 last:mb-0">{children}</h6>,
    blockquote: ({ children }) => (
      <blockquote
        className={
          onDark
            ? 'mb-2 border-l-2 border-white/40 pl-3 text-neutral-200 last:mb-0'
            : 'mb-2 border-l-2 border-neutral-300 pl-3 text-neutral-700 last:mb-0'
        }
      >
        {children}
      </blockquote>
    ),
    hr: () => <hr className={onDark ? 'my-3 border-white/20' : 'my-3 border-neutral-200'} />,
    a: ({ href, children }) => (
      <a href={href} className={linkClass} target="_blank" rel="noopener noreferrer">
        {children}
      </a>
    ),
    strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
    em: ({ children }) => <em className="italic">{children}</em>,
    del: ({ children }) => <del className="opacity-75">{children}</del>,
    code: ({ className, children, ...props }) => {
      const inline = 'inline' in props && Boolean((props as { inline?: boolean }).inline)
      if (inline) {
        return <code className={inlineCodeClass}>{children}</code>
      }
      return <code className={className}>{children}</code>
    },
    pre: ({ children }) => <pre className={blockPreClass}>{children}</pre>,
    table: ({ children }) => (
      <span className={tableShell}>
        <table className="min-w-full border-collapse text-left text-sm">{children}</table>
      </span>
    ),
    thead: ({ children }) => <thead className={onDark ? 'bg-white/10' : 'bg-neutral-100'}>{children}</thead>,
    tbody: ({ children }) => <tbody>{children}</tbody>,
    tr: ({ children }) => <tr className={onDark ? 'even:bg-white/[0.06]' : 'even:bg-neutral-50'}>{children}</tr>,
    th: ({ children }) => (
      <th className={`${thTd} font-semibold ${onDark ? 'text-white' : 'text-neutral-900'}`}>{children}</th>
    ),
    td: ({ children }) => <td className={thTd}>{children}</td>,
    img: ({ src, alt }) => (
      <img
        src={src}
        alt={alt ?? ''}
        className="my-2 max-h-64 max-w-full rounded-md object-contain"
        loading="lazy"
      />
    ),
  }
}

export function MarkdownContent({ markdown, variant = 'onLight', className }: MarkdownContentProps) {
  const components = markdownComponents(variant)
  const rootClass = ['markdown-content [&_p:first-child]:mt-0 [&_p:last-child]:mb-0', className]
    .filter(Boolean)
    .join(' ')

  return (
    <div className={rootClass}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {markdown}
      </ReactMarkdown>
    </div>
  )
}

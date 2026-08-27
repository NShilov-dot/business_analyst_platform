/**
 * Chat composer shell: one bordered card holding a borderless, auto-growing
 * textarea with a row of actions under it.
 *
 * Adapted from the 21st.dev "ai-prompt-box" component — the shape, autosize
 * and Enter-to-submit are kept; state is controlled by the parent and colours
 * come from the theme tokens. The demo's hex palette, framer-motion, radix
 * tooltip/dialog, image upload and mock voice recorder were dropped.
 */
import * as React from 'react'

import { cn } from '@/lib/utils'

// Past this the textarea stops growing and scrolls (same cap as the old max-h-40).
const MAX_HEIGHT = 160

function PromptInput({ className, children }: { className?: string; children: React.ReactNode }) {
  return (
    <div
      className={cn(
        'rounded-2xl border border-input bg-card p-2 shadow-sm transition-colors focus-within:border-ring focus-within:ring-1 focus-within:ring-ring',
        className,
      )}
    >
      {children}
    </div>
  )
}

const PromptInputTextarea = React.forwardRef<
  HTMLTextAreaElement,
  Omit<React.ComponentProps<'textarea'>, 'onSubmit'> & { onSubmit?: () => void }
>(({ className, value, onKeyDown, onSubmit, ...props }, ref) => {
  const innerRef = React.useRef<HTMLTextAreaElement>(null)
  React.useImperativeHandle(ref, () => innerRef.current as HTMLTextAreaElement)

  // Grow with the content, then scroll. Keyed on `value` so programmatic
  // edits (voice transcripts, field nudges) resize too, not only typing.
  React.useLayoutEffect(() => {
    const el = innerRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT)}px`
  }, [value])

  return (
    <textarea
      ref={innerRef}
      value={value}
      rows={1}
      onKeyDown={(e) => {
        // Enter submits, Shift+Enter inserts a newline.
        if (e.key === 'Enter' && !e.shiftKey && onSubmit) {
          e.preventDefault()
          onSubmit()
        }
        onKeyDown?.(e)
      }}
      className={cn(
        'block w-full resize-none border-0 bg-transparent px-2.5 py-2 text-base leading-relaxed placeholder:text-muted-foreground focus-visible:outline-none disabled:opacity-60 sm:text-[13.5px]',
        className,
      )}
      {...props}
    />
  )
})
PromptInputTextarea.displayName = 'PromptInputTextarea'

function PromptInputActions({ className, children }: { className?: string; children: React.ReactNode }) {
  return <div className={cn('flex items-center gap-1 pt-1', className)}>{children}</div>
}

export { PromptInput, PromptInputTextarea, PromptInputActions }

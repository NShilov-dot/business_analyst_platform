import { ApiError } from '../api/client'

export function getErrorMessage(
  err: unknown,
  fallback = 'Something went wrong. Please try again.',
): string {
  if (err instanceof ApiError) {
    const body = err.body
    if (body && typeof body === 'object') {
      const envelope = (body as Record<string, unknown>).error
      if (envelope && typeof envelope === 'object') {
        const e = envelope as Record<string, unknown>
        if (Array.isArray(e.details) && e.details.length > 0) {
          const fieldMsgs = e.details
            .map((d) =>
              d && typeof d === 'object'
                ? (d as Record<string, unknown>).message
                : undefined,
            )
            .filter((m): m is string => typeof m === 'string')
          if (fieldMsgs.length > 0) return fieldMsgs.join('. ')
        }

        if (typeof e.message === 'string') return e.message
      }
      const detail = (body as Record<string, unknown>).detail
      if (typeof detail === 'string') return detail
      const message = (body as Record<string, unknown>).message
      if (typeof message === 'string') return message
    }

    if (err.status >= 500) return 'Server error. Please try again.'
    if (err.status === 403) return 'You don’t have permission to do that.'
    if (err.status === 404) return 'This item no longer exists.'
    return fallback
  }
  if (err instanceof Error && err.message) return err.message
  return fallback
}

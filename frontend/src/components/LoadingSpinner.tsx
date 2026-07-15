export function LoadingSpinner({ label = 'Loading…' }: { label?: string }) {
  return (
    <div
      role="status"
      aria-label={label}
      className="flex justify-center py-12"
    >
      <div className="w-8 h-8 border-4 border-muted border-t-primary rounded-full animate-spin" />
      <span className="sr-only">{label}</span>
    </div>
  )
}

import { useCallback } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { XCircle } from 'lucide-react'
import { cn } from '@/lib/utils'
import {
  STATUS_META,
  WORKFLOW_ORDER,
  priorityMeta,
  type TicketStatus,
} from '@/features/tickets/model'
import { ruDate, shortSubject } from '@/features/tickets/format'
import { ticketsApi, type Ticket } from '@/api/tickets'
import { useAuth } from '@/auth/AuthProvider'
import { LoadingSpinner } from '@/components/LoadingSpinner'

const COLUMNS: TicketStatus[] = [...WORKFLOW_ORDER, 'rejected']

function TicketCard({ ticket, currentSubject }: { ticket: Ticket; currentSubject: string }) {
  const navigate = useNavigate()
  const prio = priorityMeta(ticket.priority)
  const authorLabel = shortSubject(ticket.author_id, currentSubject)
  const shortId = '#' + ticket.id.slice(0, 8)
  const createdDate = ruDate(ticket.created_at)
  const open = () => navigate(`/tickets/${ticket.id}`)

  return (
    <div
      role="article"
      onClick={open}
      // Left accent = priority (inline so it survives the hover border-color change).
      style={{ borderLeftColor: prio.color, borderLeftWidth: 3 }}
      className="group cursor-pointer rounded-[13px] border border-border bg-card p-[13px] shadow-sm transition-colors hover:border-primary/40 hover:bg-primary/[0.03] focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          open()
        }
      }}
      aria-label={`Заявка ${shortId}: ${ticket.title}`}
    >
      {/* Header: short id + priority chip */}
      <div className="mb-1.5 flex items-center gap-2">
        <span className="font-mono text-[11px] font-semibold text-muted-foreground">{shortId}</span>
        <span
          className="ml-auto flex items-center gap-[5px] text-[10.5px] font-semibold"
          style={{ color: prio.color }}
        >
          <span className="h-[7px] w-[7px] rounded-full" style={{ background: prio.color }} />
          {prio.label}
        </span>
      </div>

      {/* Title */}
      <div className="line-clamp-2 text-[13px] font-semibold leading-snug">{ticket.title}</div>

      {/* One-line description preview — helps scan by content, not just title */}
      {ticket.description && (
        <div className="mt-1 line-clamp-1 text-[11.5px] leading-snug text-muted-foreground">
          {ticket.description}
        </div>
      )}

      {/* Footer: date + author */}
      <div className="mt-2.5 flex items-center gap-2 border-t border-border pt-2.5">
        <span className="text-[11px] text-muted-foreground">{createdDate}</span>
        <span className="ml-auto max-w-[90px] truncate font-mono text-[11px] text-muted-foreground">
          {authorLabel}
        </span>
      </div>
    </div>
  )
}

export default function BoardPage() {
  const { user } = useAuth()

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['tickets', 'board'],
    queryFn: () => ticketsApi.list({ limit: 100 }),
  })

  const tickets: Ticket[] = data?.data ?? []

  // Drill-down from the dashboard: ?status=<k> highlights + scrolls one column.
  const [params] = useSearchParams()
  const focusRaw = params.get('status')
  const focusStatus =
    focusRaw && COLUMNS.includes(focusRaw as TicketStatus) ? (focusRaw as TicketStatus) : null
  const focusRef = useCallback((el: HTMLDivElement | null) => {
    el?.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' })
  }, [])

  if (isLoading) {
    return (
      <div className="animate-vfade">
        <LoadingSpinner label="Загрузка заявок…" />
      </div>
    )
  }

  if (isError) {
    return (
      <div className="animate-vfade">
        <div className="mx-auto mt-6 max-w-[460px] rounded-2xl border border-border bg-card p-8 text-center">
          <XCircle className="mx-auto mb-3 h-8 w-8 text-destructive" />
          <div className="text-sm font-medium">Не удалось загрузить заявки</div>
          <div className="mt-1 text-[12.5px] text-muted-foreground">
            Проверьте соединение и попробуйте снова.
          </div>
          <button
            type="button"
            onClick={() => void refetch()}
            className="mt-4 rounded-xl border border-input bg-card px-4 py-2 text-[13px] font-semibold hover:bg-muted"
          >
            Повторить
          </button>
        </div>
      </div>
    )
  }

  // Group once (single pass) instead of filtering per column.
  const byStatus = {} as Record<TicketStatus, Ticket[]>
  for (const s of COLUMNS) byStatus[s] = []
  for (const t of tickets) byStatus[t.status]?.push(t)

  return (
    <div className="animate-vfade">
      <div className="flex items-start gap-3.5 overflow-x-auto pb-3">
        {COLUMNS.map((status) => {
          const list = byStatus[status]
          const meta = STATUS_META[status]
          const focused = status === focusStatus
          return (
            <div
              key={status}
              ref={focused ? focusRef : undefined}
              // Colored top rule gives each lane a scannable status identity.
              style={{ borderTopColor: meta.color, borderTopWidth: 3 }}
              className={cn(
                'flex w-[268px] flex-none flex-col overflow-hidden rounded-2xl border border-border bg-muted/40',
                focused && 'ring-2 ring-primary ring-offset-2 ring-offset-background',
              )}
            >
              {/* Column header */}
              <div className="flex items-center gap-2 border-b border-border bg-card/70 px-3.5 py-2.5">
                <span className="text-[13px] font-semibold">{meta.label}</span>
                <span className="ml-auto min-w-[22px] rounded-full border border-border bg-card px-[9px] py-px text-center text-[11.5px] font-semibold tabular-nums text-muted-foreground">
                  {list.length}
                </span>
              </div>

              {/* Cards */}
              <div className="flex flex-col gap-2.5 p-3">
                {list.length === 0 ? (
                  <div className="rounded-xl border border-dashed border-border/70 py-5 text-center text-[11px] text-muted-foreground">
                    Пусто
                  </div>
                ) : (
                  list.map((t) => (
                    <TicketCard key={t.id} ticket={t} currentSubject={user.subject} />
                  ))
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

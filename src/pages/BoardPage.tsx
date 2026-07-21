import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { cn } from '@/lib/utils'
import {
  STATUS_META,
  WORKFLOW_ORDER,
  priorityMeta,
  type TicketStatus,
} from '@/features/tickets/model'
import { ticketsApi, type Ticket } from '@/api/tickets'
import { useAuth } from '@/auth/AuthProvider'
import { LoadingSpinner } from '@/components/LoadingSpinner'

const COLUMNS: TicketStatus[] = [...WORKFLOW_ORDER, 'rejected']

function TicketCard({ ticket, currentSubject }: { ticket: Ticket; currentSubject: string }) {
  const navigate = useNavigate()
  const prio = priorityMeta(ticket.priority)
  const authorLabel =
    ticket.author_id === currentSubject ? 'Вы' : '#' + ticket.author_id.slice(0, 8)
  const shortId = '#' + ticket.id.slice(0, 8)
  const createdDate = new Date(ticket.created_at).toLocaleDateString('ru-RU', {
    day: '2-digit',
    month: 'short',
  })

  return (
    <div
      role="article"
      onClick={() => navigate(`/tickets/${ticket.id}`)}
      className="cursor-pointer rounded-[13px] border border-border bg-card p-[13px] shadow-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          navigate(`/tickets/${ticket.id}`)
        }
      }}
      aria-label={`Заявка ${shortId}: ${ticket.title}`}
    >
      {/* Header: short id + priority chip */}
      <div className="mb-2 flex items-center gap-2">
        <span className="font-mono text-[11px] font-semibold text-muted-foreground">
          {shortId}
        </span>
        <span
          className="ml-auto flex items-center gap-[5px] text-[10.5px] font-semibold"
          style={{ color: prio.color }}
        >
          <span
            className="h-[7px] w-[7px] rounded-full"
            style={{ background: prio.color }}
          />
          {prio.label}
        </span>
      </div>

      {/* Title */}
      <div className="mb-2.5 line-clamp-2 text-[13px] font-semibold leading-snug">
        {ticket.title}
      </div>

      {/* Footer: date + author */}
      <div className="flex items-center gap-2 border-t border-border pt-2.5">
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

  const { data, isLoading, isError } = useQuery({
    queryKey: ['tickets', 'board'],
    queryFn: () => ticketsApi.list({ limit: 100 }),
  })

  const tickets: Ticket[] = data?.data ?? []

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
        <p className="px-3 py-6 text-center text-sm text-muted-foreground">
          Не удалось загрузить заявки
        </p>
      </div>
    )
  }

  return (
    <div className="animate-vfade">
      <div className="flex items-start gap-4 overflow-x-auto pb-3">
        {COLUMNS.map((status) => {
          const list = tickets.filter((t) => t.status === status)
          const meta = STATUS_META[status]
          return (
            <div
              key={status}
              className="w-[266px] flex-none rounded-2xl border border-border bg-muted/60 p-3"
            >
              {/* Column header */}
              <div className="flex items-center gap-2 px-1.5 pb-3 pt-1">
                <span
                  className="h-[9px] w-[9px] rounded-[3px]"
                  style={{ background: meta.color }}
                />
                <span className="text-[13px] font-semibold">{meta.label}</span>
                <span className="ml-auto rounded-full border border-border bg-card px-[9px] py-px text-[11.5px] font-semibold text-muted-foreground">
                  {list.length}
                </span>
              </div>

              {/* Cards */}
              <div
                className={cn('flex flex-col gap-2.5', list.length === 0 && 'min-h-6')}
              >
                {list.map((t) => (
                  <TicketCard key={t.id} ticket={t} currentSubject={user.subject} />
                ))}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

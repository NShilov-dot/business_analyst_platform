import {
  BadgeCheck,
  Calendar,
  Circle,
  Clock,
  FileText,
  FilePlus,
  FileEdit,
  FileX,
  GitMerge,
  ListChecks,
  PlayCircle,
  Send,
  ShieldCheck,
  Shuffle,
  ThumbsUp,
  Undo2,
  UserCog,
  XCircle,
  type LucideIcon,
} from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { STATUS_META, WORKFLOW_ORDER, type TicketStatus } from '@/features/tickets/model'
import { ticketsApi } from '@/api/tickets'
import { analyticsApi, type ActivityItem } from '@/api/analytics'
import { useAuth } from '@/auth/AuthProvider'
import { LoadingSpinner } from '@/components/LoadingSpinner'

// ---------------------------------------------------------------------------
// Action metadata for the activity feed
// ---------------------------------------------------------------------------
interface ActionMeta {
  label: string
  icon: LucideIcon
  color: string
  soft: string
}

const ACTION_META: Record<string, ActionMeta> = {
  created: {
    label: 'создана',
    icon: FilePlus,
    color: '#64748B',
    soft: 'bg-slate-100 dark:bg-slate-800',
  },
  updated: {
    label: 'обновлена',
    icon: FileEdit,
    color: '#6366F1',
    soft: 'bg-indigo-50 dark:bg-indigo-950',
  },
  submission_replaced: {
    label: 'интейк обновлён',
    icon: FileEdit,
    color: '#8B5CF6',
    soft: 'bg-violet-50 dark:bg-violet-950',
  },
  submitted: {
    label: 'отправлена на триаж',
    icon: Send,
    color: '#D97706',
    soft: 'bg-amber-50 dark:bg-amber-950',
  },
  triage_accepted: {
    label: 'принята (ТЗ)',
    icon: ShieldCheck,
    color: '#0891B2',
    soft: 'bg-cyan-50 dark:bg-cyan-950',
  },
  returned_for_refinement: {
    label: 'возвращена на доработку',
    icon: Undo2,
    color: '#D97706',
    soft: 'bg-amber-50 dark:bg-amber-950',
  },
  rejected: {
    label: 'отклонена',
    icon: XCircle,
    color: '#DC2626',
    soft: 'bg-red-50 dark:bg-red-950',
  },
  assignment_changed: {
    label: 'назначение изменено',
    icon: UserCog,
    color: '#64748B',
    soft: 'bg-slate-100 dark:bg-slate-800',
  },
  spec_approval_attested: {
    label: 'ТЗ подписано',
    icon: ShieldCheck,
    color: '#6366F1',
    soft: 'bg-indigo-50 dark:bg-indigo-950',
  },
  work_started: {
    label: 'взята в работу',
    icon: PlayCircle,
    color: '#8B5CF6',
    soft: 'bg-violet-50 dark:bg-violet-950',
  },
  work_finished: {
    label: 'работа завершена',
    icon: BadgeCheck,
    color: '#0D9488',
    soft: 'bg-teal-50 dark:bg-teal-950',
  },
  acceptance_requested: {
    label: 'запрошена приёмка',
    icon: GitMerge,
    color: '#0891B2',
    soft: 'bg-cyan-50 dark:bg-cyan-950',
  },
  formal_dod_attested: {
    label: 'DoD подписан',
    icon: ListChecks,
    color: '#16A34A',
    soft: 'bg-green-50 dark:bg-green-950',
  },
  business_value_attested: {
    label: 'ценность подтверждена',
    icon: ThumbsUp,
    color: '#16A34A',
    soft: 'bg-green-50 dark:bg-green-950',
  },
  returned_to_work: {
    label: 'возвращена в работу',
    icon: Shuffle,
    color: '#D97706',
    soft: 'bg-amber-50 dark:bg-amber-950',
  },
  closed: {
    label: 'закрыта',
    icon: FileX,
    color: '#16A34A',
    soft: 'bg-green-50 dark:bg-green-950',
  },
}

function actionMeta(action: string): ActionMeta {
  return (
    ACTION_META[action] ?? {
      label: action,
      icon: Circle,
      color: '#94A3B8',
      soft: 'bg-slate-100 dark:bg-slate-800',
    }
  )
}

// ---------------------------------------------------------------------------
// ActivityRow sub-component
// ---------------------------------------------------------------------------
function ActivityRow({
  item,
  subject,
  onClick,
}: {
  item: ActivityItem
  subject: string
  onClick: () => void
}) {
  const meta = actionMeta(item.action)
  const Icon = meta.icon

  const title = item.ticket_title ?? `#${item.entity_id.slice(0, 8)}`
  const rowText = `${title} — ${meta.label}`

  let actorLabel: string
  if (item.actor.startsWith('system:')) {
    actorLabel = item.actor
  } else if (item.actor === subject) {
    actorLabel = 'Вы'
  } else {
    actorLabel = `#${item.actor.slice(0, 8)}`
  }

  const timeLabel = new Date(item.occurred_at).toLocaleString('ru-RU', {
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  })

  return (
    <button
      onClick={onClick}
      className="flex w-full items-start gap-3 rounded-xl px-2 py-2 text-left transition-colors hover:bg-muted/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <span
        className={`mt-0.5 flex h-7 w-7 flex-none items-center justify-center rounded-lg ${meta.soft}`}
      >
        <Icon className="h-3.5 w-3.5" style={{ color: meta.color }} />
      </span>
      <div className="min-w-0 flex-1">
        <div className="truncate text-[13px] font-medium leading-snug">{rowText}</div>
        <div className="mt-0.5 text-[11.5px] text-muted-foreground">
          {actorLabel} · {timeLabel}
        </div>
      </div>
    </button>
  )
}

// ---------------------------------------------------------------------------
// FlowChart sub-component
// ---------------------------------------------------------------------------
function FlowChart({ points }: { points: { week_start: string; created: number; closed: number }[] }) {
  const W = 560
  const H = 152
  const PAD_LEFT = 6
  const PAD_RIGHT = 6
  const PAD_TOP = 12
  const PAD_BOTTOM = 24

  const innerW = W - PAD_LEFT - PAD_RIGHT
  const innerH = H - PAD_TOP - PAD_BOTTOM

  const n = points.length
  const maxVal = Math.max(1, ...points.map((p) => p.created), ...points.map((p) => p.closed))

  // Map a value to SVG y coordinate (0 at bottom)
  const toY = (v: number) => PAD_TOP + innerH - (v / maxVal) * innerH
  const toX = (i: number) => PAD_LEFT + (n <= 1 ? innerW / 2 : (i / (n - 1)) * innerW)

  // Build SVG polyline points string
  function polylinePoints(series: 'created' | 'closed') {
    return points.map((p, i) => `${toX(i).toFixed(1)},${toY(p[series]).toFixed(1)}`).join(' ')
  }

  // Build closed area path (line + baseline)
  function areaPath(series: 'created' | 'closed') {
    if (n === 0) return ''
    const linePoints = points.map((p, i) => `${toX(i).toFixed(1)},${toY(p[series]).toFixed(1)}`)
    const baselineY = (PAD_TOP + innerH).toFixed(1)
    return [
      `M ${toX(0).toFixed(1)} ${baselineY}`,
      `L ${linePoints.join(' L ')}`,
      `L ${toX(n - 1).toFixed(1)} ${baselineY}`,
      'Z',
    ].join(' ')
  }

  // X-axis labels: first and last week_start formatted as dd.MM
  function formatWeek(dateStr: string) {
    const [, month, day] = dateStr.split('-')
    return `${day}.${month}`
  }

  const gridLines = 4
  const gradCreatedId = 'grad-created'
  const gradClosedId = 'grad-closed'

  return (
    <div className="mt-4">
      {/* Legend */}
      <div className="mb-2 flex items-center gap-4 text-[12px]">
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2 w-2 rounded-full bg-[#6366F1]" />
          <span className="text-muted-foreground">Создано</span>
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2 w-2 rounded-full bg-[#16A34A]" />
          <span className="text-muted-foreground">Закрыто</span>
        </span>
      </div>

      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full"
        preserveAspectRatio="none"
        aria-hidden="true"
      >
        <defs>
          <linearGradient id={gradCreatedId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#6366F1" stopOpacity="0.25" />
            <stop offset="100%" stopColor="#6366F1" stopOpacity="0.02" />
          </linearGradient>
          <linearGradient id={gradClosedId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#16A34A" stopOpacity="0.18" />
            <stop offset="100%" stopColor="#16A34A" stopOpacity="0.02" />
          </linearGradient>
        </defs>

        {/* Horizontal grid lines */}
        {Array.from({ length: gridLines + 1 }).map((_, gi) => {
          const y = PAD_TOP + (gi / gridLines) * innerH
          return (
            <line
              key={gi}
              x1={PAD_LEFT}
              y1={y.toFixed(1)}
              x2={W - PAD_RIGHT}
              y2={y.toFixed(1)}
              stroke="currentColor"
              strokeOpacity="0.06"
              strokeWidth="1"
            />
          )
        })}

        {n > 0 && (
          <>
            {/* Gradient area — created (behind) */}
            <path d={areaPath('created')} fill={`url(#${gradCreatedId})`} />

            {/* Gradient area — closed (behind) */}
            <path d={areaPath('closed')} fill={`url(#${gradClosedId})`} />

            {/* Line — created */}
            <polyline
              points={polylinePoints('created')}
              fill="none"
              stroke="#6366F1"
              strokeWidth="1.8"
              strokeLinejoin="round"
              strokeLinecap="round"
            />

            {/* Line — closed */}
            <polyline
              points={polylinePoints('closed')}
              fill="none"
              stroke="#16A34A"
              strokeWidth="1.8"
              strokeLinejoin="round"
              strokeLinecap="round"
            />
          </>
        )}

        {/* X-axis labels */}
        {n >= 2 && (
          <>
            <text
              x={toX(0).toFixed(1)}
              y={H - 4}
              textAnchor="start"
              fontSize="10"
              fill="currentColor"
              fillOpacity="0.45"
            >
              {formatWeek(points[0].week_start)}
            </text>
            <text
              x={toX(n - 1).toFixed(1)}
              y={H - 4}
              textAnchor="end"
              fontSize="10"
              fill="currentColor"
              fillOpacity="0.45"
            >
              {formatWeek(points[n - 1].week_start)}
            </text>
          </>
        )}
      </svg>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------
export default function DashboardPage() {
  const navigate = useNavigate()
  const { user } = useAuth()

  // Primary data: all tickets (up to 100) for status counts
  const ticketsQ = useQuery({
    queryKey: ['tickets', 'dashboard'],
    queryFn: () => ticketsApi.list({ limit: 100 }),
  })

  // Analytics: ticket flow chart
  const flowQ = useQuery({
    queryKey: ['analytics', 'flow'],
    queryFn: () => analyticsApi.ticketFlow(24),
  })

  // Analytics: activity feed
  const actQ = useQuery({
    queryKey: ['analytics', 'activity'],
    queryFn: () => analyticsApi.activity(8),
  })

  const tickets = ticketsQ.data?.data ?? []
  const flowPoints = flowQ.data?.data.points ?? []
  const actItems = actQ.data?.data ?? []

  // Build per-status counts
  const counts = {} as Record<TicketStatus, number>
  for (const key of Object.keys(STATUS_META) as TicketStatus[]) counts[key] = 0
  for (const t of tickets) counts[t.status] += 1
  const total = tickets.length

  // Active (non-terminal) = everything except closed and rejected
  const activeCount =
    counts.created +
    counts.triage +
    counts.spec_approval +
    counts.in_progress +
    counts.change_capture +
    counts.acceptance

  // Donut segments over the statuses that actually have tickets
  const present = WORKFLOW_ORDER.filter((k) => counts[k] > 0)
  // Also include rejected if there are any
  const presentWithRejected = [...present, ...(counts.rejected > 0 ? (['rejected'] as TicketStatus[]) : [])]
  const C = 2 * Math.PI * 52
  let off = 0
  const segments =
    total === 0
      ? []
      : presentWithRejected.map((k) => {
          const len = (counts[k] / total) * C
          const seg = {
            key: k,
            color: STATUS_META[k].color,
            dash: `${len.toFixed(1)} ${(C - len).toFixed(1)}`,
            offset: (-off).toFixed(1),
          }
          off += len
          return seg
        })

  const maxCount = Math.max(1, ...WORKFLOW_ORDER.map((k) => counts[k]))

  if (ticketsQ.isLoading) {
    return (
      <div className="animate-vfade">
        <LoadingSpinner label="Загрузка дашборда…" />
      </div>
    )
  }

  if (ticketsQ.isError) {
    return (
      <div className="animate-vfade">
        <div className="mx-auto mt-6 max-w-[460px] rounded-2xl border border-border bg-card p-8 text-center">
          <XCircle className="mx-auto mb-3 h-8 w-8 text-destructive" />
          <div className="text-sm font-medium">Не удалось загрузить данные дашборда</div>
          <div className="mt-1 text-[12.5px] text-muted-foreground">
            Проверьте соединение и попробуйте снова.
          </div>
          <button
            type="button"
            onClick={() => void ticketsQ.refetch()}
            className="mt-4 rounded-xl border border-input bg-card px-4 py-2 text-[13px] font-semibold hover:bg-muted"
          >
            Повторить
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="animate-vfade">
      {/* KPI row — 4 cards, all derived from real data */}
      <div className="mb-5 grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4">
        {/* 1. Total tickets */}
        <div className="rounded-2xl border border-border bg-card p-[18px]">
          <div className="mb-3.5 flex items-center justify-between">
            <div className="flex h-[34px] w-[34px] items-center justify-center rounded-[10px] bg-muted">
              <FileText className="h-[19px] w-[19px]" />
            </div>
          </div>
          <div className="text-[27px] font-bold leading-none tracking-tight">{total}</div>
          <div className="mt-1.5 text-[12.5px] font-medium leading-tight">Всего заявок</div>
          <div className="mt-0.5 text-[11px] text-muted-foreground">в системе</div>
        </div>

        {/* 2. Active (non-terminal) */}
        <div className="rounded-2xl border border-border bg-card p-[18px]">
          <div className="mb-3.5 flex items-center justify-between">
            <div className="flex h-[34px] w-[34px] items-center justify-center rounded-[10px] bg-muted">
              <Clock className="h-[19px] w-[19px]" />
            </div>
          </div>
          <div className="text-[27px] font-bold leading-none tracking-tight">{activeCount}</div>
          <div className="mt-1.5 text-[12.5px] font-medium leading-tight">В работе</div>
          <div className="mt-0.5 text-[11px] text-muted-foreground">все незакрытые статусы</div>
        </div>

        {/* 3. Closed */}
        <div className="rounded-2xl border border-border bg-card p-[18px]">
          <div className="mb-3.5 flex items-center justify-between">
            <div className="flex h-[34px] w-[34px] items-center justify-center rounded-[10px] bg-muted">
              <BadgeCheck className="h-[19px] w-[19px]" />
            </div>
          </div>
          <div className="text-[27px] font-bold leading-none tracking-tight">{counts.closed}</div>
          <div className="mt-1.5 text-[12.5px] font-medium leading-tight">Закрыто</div>
          <div className="mt-0.5 text-[11px] text-muted-foreground">завершены успешно</div>
        </div>

        {/* 4. Rejected */}
        <div className="rounded-2xl border border-border bg-card p-[18px]">
          <div className="mb-3.5 flex items-center justify-between">
            <div className="flex h-[34px] w-[34px] items-center justify-center rounded-[10px] bg-muted">
              <ListChecks className="h-[19px] w-[19px]" />
            </div>
          </div>
          <div className="text-[27px] font-bold leading-none tracking-tight">{counts.rejected}</div>
          <div className="mt-1.5 text-[12.5px] font-medium leading-tight">Отклонено</div>
          <div className="mt-0.5 text-[11px] text-muted-foreground">дубли / нецелесообразные</div>
        </div>
      </div>

      <div className="mb-4 grid grid-cols-1 gap-4 lg:grid-cols-[1.55fr_1fr]">
        {/* Flow chart — REAL data from /v1/analytics/ticket-flow */}
        <div className="rounded-2xl border border-border bg-card px-[22px] py-5">
          <div className="mb-2 flex items-start justify-between">
            <div>
              <div className="flex items-center gap-2">
                <span className="text-[15px] font-semibold">Поток заявок за период</span>
              </div>
              <div className="mt-0.5 text-[12.5px] text-muted-foreground">
                Создано против закрыто по неделям.
              </div>
            </div>
            <div className="hidden items-center gap-1.5 rounded-[9px] border border-border px-[11px] py-[7px] text-[12.5px] font-medium sm:flex">
              <Calendar className="h-4 w-4 text-muted-foreground" />
              Еженедельно
            </div>
          </div>

          {flowQ.isLoading ? (
            <div className="mt-4 flex min-h-[152px] animate-pulse items-center justify-center rounded-xl bg-muted/30">
              <div className="h-3 w-24 rounded bg-muted" />
            </div>
          ) : flowQ.isError ? (
            <div className="mt-4 flex min-h-[152px] flex-col items-center justify-center gap-2 rounded-xl bg-muted/30 text-center">
              <XCircle className="h-6 w-6 text-destructive" />
              <p className="text-[12.5px] text-muted-foreground">Не удалось загрузить поток заявок</p>
              <button
                type="button"
                onClick={() => void flowQ.refetch()}
                className="text-[12px] font-semibold text-primary hover:underline"
              >
                Повторить
              </button>
            </div>
          ) : (
            <FlowChart points={flowPoints} />
          )}
        </div>

        {/* Status donut — REAL data */}
        <div className="rounded-2xl border border-border bg-card px-[22px] py-5">
          <div className="text-[15px] font-semibold">Распределение по статусам</div>
          <div className="mb-1.5 mt-0.5 text-[12.5px] text-muted-foreground">
            Живой срез портфеля заявок.
          </div>
          <div className="flex items-center gap-[18px]">
            <div className="relative flex-none">
              <svg viewBox="0 0 140 140" className="h-[132px] w-[132px]">
                <circle
                  cx="70"
                  cy="70"
                  r="52"
                  fill="none"
                  stroke="hsl(var(--muted))"
                  strokeWidth="18"
                />
                {segments.map((seg) => (
                  <circle
                    key={seg.key}
                    cx="70"
                    cy="70"
                    r="52"
                    fill="none"
                    stroke={seg.color}
                    strokeWidth="18"
                    strokeDasharray={seg.dash}
                    strokeDashoffset={seg.offset}
                    transform="rotate(-90 70 70)"
                  />
                ))}
              </svg>
              <div className="absolute inset-0 flex flex-col items-center justify-center">
                <div className="text-[22px] font-bold leading-none">{total}</div>
                <div className="text-[10.5px] text-muted-foreground">заявок</div>
              </div>
            </div>
            <div className="flex flex-1 flex-col gap-[7px]">
              {total === 0 ? (
                <p className="text-[12px] text-muted-foreground">Нет заявок</p>
              ) : (
                presentWithRejected.map((k) => (
                  <div key={k} className="flex items-center gap-2 text-xs">
                    <span
                      className="h-[9px] w-[9px] flex-none rounded-[3px]"
                      style={{ background: STATUS_META[k].color }}
                    />
                    <span className="flex-1 text-muted-foreground">{STATUS_META[k].label}</span>
                    <span className="font-semibold">{counts[k]}</span>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1.4fr_1fr]">
        {/* Lifecycle funnel — REAL data */}
        <div className="rounded-2xl border border-border bg-card px-[22px] py-5">
          <div className="mb-0.5 text-[15px] font-semibold">Воронка жизненного цикла</div>
          <div className="mb-4 text-[12.5px] text-muted-foreground">
            Сколько заявок на каждом статусе прямо сейчас.
          </div>
          <div className="flex flex-col gap-[13px]">
            {WORKFLOW_ORDER.map((k) => (
              <div key={k} className="flex items-center gap-3">
                <div className="flex w-[110px] flex-none items-center gap-2 text-[12.5px] font-medium sm:w-[150px]">
                  <span
                    className="h-[9px] w-[9px] rounded-[3px]"
                    style={{ background: STATUS_META[k].color }}
                  />
                  {STATUS_META[k].label}
                </div>
                <div className="h-2.5 flex-1 overflow-hidden rounded-md bg-muted">
                  <div
                    className="h-full rounded-md transition-all duration-500"
                    style={{
                      width: `${((counts[k] / maxCount) * 100).toFixed(0)}%`,
                      background: STATUS_META[k].color,
                    }}
                  />
                </div>
                <div className="w-[26px] text-right text-[13px] font-semibold">{counts[k]}</div>
              </div>
            ))}
            {/* Rejected shown separately as a terminal branch */}
            {counts.rejected > 0 && (
              <div className="flex items-center gap-3 opacity-70">
                <div className="flex w-[110px] flex-none items-center gap-2 text-[12.5px] font-medium sm:w-[150px]">
                  <span
                    className="h-[9px] w-[9px] rounded-[3px]"
                    style={{ background: STATUS_META.rejected.color }}
                  />
                  {STATUS_META.rejected.label}
                </div>
                <div className="h-2.5 flex-1 overflow-hidden rounded-md bg-muted">
                  <div
                    className="h-full rounded-md transition-all duration-500"
                    style={{
                      width: `${((counts.rejected / maxCount) * 100).toFixed(0)}%`,
                      background: STATUS_META.rejected.color,
                    }}
                  />
                </div>
                <div className="w-[26px] text-right text-[13px] font-semibold">{counts.rejected}</div>
              </div>
            )}
          </div>
        </div>

        {/* Activity feed — REAL data from /v1/analytics/activity */}
        <div className="rounded-2xl border border-border bg-card px-[22px] py-5">
          <div className="mb-0.5 text-[15px] font-semibold">Последние события</div>
          <div className="mb-3 text-[12.5px] text-muted-foreground">Аудит-лог по тикетам.</div>

          {actQ.isLoading ? (
            <div className="flex flex-col gap-2">
              {Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="flex animate-pulse items-start gap-3 px-2 py-2">
                  <div className="mt-0.5 h-7 w-7 flex-none rounded-lg bg-muted" />
                  <div className="flex-1 space-y-1.5">
                    <div className="h-3 w-3/4 rounded bg-muted" />
                    <div className="h-2.5 w-1/2 rounded bg-muted" />
                  </div>
                </div>
              ))}
            </div>
          ) : actQ.isError ? (
            <div className="flex min-h-[120px] flex-col items-center justify-center gap-2">
              <XCircle className="h-6 w-6 text-destructive" />
              <p className="text-[13px] text-muted-foreground">Не удалось загрузить события</p>
              <button
                type="button"
                onClick={() => void actQ.refetch()}
                className="text-[12px] font-semibold text-primary hover:underline"
              >
                Повторить
              </button>
            </div>
          ) : actItems.length === 0 ? (
            <div className="flex min-h-[120px] items-center justify-center">
              <p className="text-[13px] text-muted-foreground">Пока нет событий</p>
            </div>
          ) : (
            <div className="flex flex-col gap-0.5">
              {actItems.map((item) => (
                <ActivityRow
                  key={item.entry_id}
                  item={item}
                  subject={user.subject}
                  onClick={() => navigate('/tickets/' + item.entity_id)}
                />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

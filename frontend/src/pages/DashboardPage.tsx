import {
  ArrowUpRight,
  BadgeCheck,
  Clock,
  FileText,
  ListChecks,
  RefreshCw,
  Sparkles,
  XCircle,
  type LucideIcon,
} from 'lucide-react'
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { cn } from '@/lib/utils'
import { STATUS_META, WORKFLOW_ORDER, type TicketStatus } from '@/features/tickets/model'
import { ruDateTime, shortSubject } from '@/features/tickets/format'
import { actionMeta } from '@/features/tickets/actions'
import { ticketsApi } from '@/api/tickets'
import { analyticsApi, type ActivityItem } from '@/api/analytics'
import { useAuth } from '@/auth/AuthProvider'
import { LoadingSpinner } from '@/components/LoadingSpinner'

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

  const actorLabel = shortSubject(item.actor, subject)
  const timeLabel = ruDateTime(item.occurred_at)

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
  const sumCreated = points.reduce((s, p) => s + p.created, 0)
  const sumClosed = points.reduce((s, p) => s + p.closed, 0)

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
      {/* Legend — with period totals so the values are readable without a tooltip */}
      <div className="mb-2 flex items-center gap-4 text-[12px]">
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2 w-2 rounded-full bg-[#6366F1]" />
          <span className="text-muted-foreground">Создано</span>
          <span className="font-semibold tabular-nums">{sumCreated}</span>
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2 w-2 rounded-full bg-[#16A34A]" />
          <span className="text-muted-foreground">Закрыто</span>
          <span className="font-semibold tabular-nums">{sumClosed}</span>
        </span>
      </div>

      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="h-[120px] w-full"
        preserveAspectRatio="none"
        role="img"
        aria-label={`Поток заявок за ${n} недель: всего создано ${sumCreated}, закрыто ${sumClosed}.`}
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
// KPI card — clickable, drills down to the board (Nielsen #3 user control,
// #6 recognition over recall). Extracted so all four stay consistent (#4).
// ---------------------------------------------------------------------------
function KpiCard({
  icon: Icon,
  value,
  label,
  hint,
  iconClass,
  onClick,
  title,
}: {
  icon: LucideIcon
  value: number
  label: string
  hint: string
  iconClass?: string
  onClick: () => void
  title: string
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      className="group rounded-2xl border border-border bg-card p-[18px] text-left transition-colors hover:border-primary/40 hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <div className="mb-3.5 flex items-center justify-between">
        <div
          className={cn(
            'flex h-[34px] w-[34px] items-center justify-center rounded-[10px] bg-muted',
            iconClass,
          )}
        >
          <Icon className="h-[19px] w-[19px]" />
        </div>
        <ArrowUpRight className="h-4 w-4 text-transparent transition-colors group-hover:text-muted-foreground" />
      </div>
      <div className="text-[27px] font-bold leading-none tracking-tight tabular-nums">{value}</div>
      <div className="mt-1.5 text-[12.5px] font-medium leading-tight">{label}</div>
      <div className="mt-0.5 text-[11px] text-muted-foreground">{hint}</div>
    </button>
  )
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------
export default function DashboardPage() {
  const navigate = useNavigate()
  const { user } = useAuth()

  // Flow-chart period (Nielsen #7 flexibility) — drives the analytics query.
  const [weeks, setWeeks] = useState(24)

  // Primary data: all tickets (up to 100) for status counts
  const ticketsQ = useQuery({
    queryKey: ['tickets', 'dashboard'],
    queryFn: () => ticketsApi.list({ limit: 100 }),
  })

  // Analytics: ticket flow chart
  const flowQ = useQuery({
    queryKey: ['analytics', 'flow', weeks],
    queryFn: () => analyticsApi.ticketFlow(weeks),
  })

  // Analytics: activity feed
  const actQ = useQuery({
    queryKey: ['analytics', 'activity'],
    queryFn: () => analyticsApi.activity(8),
  })

  // Freshness + manual refresh (Nielsen #1 visibility of system status).
  const refreshing = ticketsQ.isFetching || flowQ.isFetching || actQ.isFetching
  const lastUpdated = Math.max(ticketsQ.dataUpdatedAt, flowQ.dataUpdatedAt, actQ.dataUpdatedAt)
  const refreshAll = () => {
    void ticketsQ.refetch()
    void flowQ.refetch()
    void actQ.refetch()
  }

  // Drill-down: jump to the board, optionally focused on one status.
  const goBoard = (status?: TicketStatus) =>
    navigate(status ? `/board?status=${status}` : '/board')

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
      {/* Primary action — filing a request is the product's core job */}
      <div className="mb-5 flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="text-[15px] font-semibold">Обзор</div>
          <div className="truncate text-[12.5px] text-muted-foreground">
            Портфель заявок и последние события.
          </div>
        </div>
        <div className="flex flex-none items-center gap-2">
          <button
            type="button"
            onClick={refreshAll}
            disabled={refreshing}
            aria-label="Обновить данные дашборда"
            title={
              lastUpdated
                ? `Обновлено в ${new Date(lastUpdated).toLocaleTimeString('ru-RU', {
                    hour: '2-digit',
                    minute: '2-digit',
                  })} — нажмите, чтобы обновить`
                : 'Обновить данные'
            }
            className="inline-flex items-center gap-1.5 rounded-xl border border-input bg-card px-3 py-2.5 text-[12px] font-medium text-muted-foreground hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60"
          >
            <RefreshCw className={cn('h-4 w-4', refreshing && 'animate-spin')} />
            <span className="hidden tabular-nums sm:inline">
              {lastUpdated
                ? new Date(lastUpdated).toLocaleTimeString('ru-RU', {
                    hour: '2-digit',
                    minute: '2-digit',
                  })
                : '—'}
            </span>
          </button>
          <button
            type="button"
            onClick={() => navigate('/intake')}
            className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-[13px] font-bold text-primary-foreground hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <Sparkles className="h-4 w-4" />
            <span className="hidden sm:inline">Создать&nbsp;</span>заявку
          </button>
        </div>
      </div>

      {total === 0 && (
        <div className="mb-5 rounded-2xl border-[1.5px] border-primary bg-card p-6 text-center">
          <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-2xl bg-primary text-primary-foreground">
            <Sparkles className="h-6 w-6" />
          </div>
          <div className="text-[16px] font-bold">Заявок пока нет</div>
          <div className="mx-auto mt-1 max-w-[420px] text-[13px] text-muted-foreground">
            Опишите задачу ассистенту — он задаст вопросы и соберёт первую бизнес-заявку.
          </div>
          <button
            type="button"
            onClick={() => navigate('/intake')}
            className="mt-4 inline-flex items-center gap-2 rounded-xl bg-primary px-5 py-2.5 text-[13.5px] font-bold text-primary-foreground hover:bg-primary/90"
          >
            <Sparkles className="h-4 w-4" />
            Создать заявку
          </button>
        </div>
      )}

      {/* KPI row — 4 cards, all derived from real data; each drills into the board */}
      <div className="mb-5 grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4">
        <KpiCard
          icon={FileText}
          value={total}
          label="Всего заявок"
          hint="в системе"
          onClick={() => goBoard()}
          title="Открыть доску заявок"
        />
        <KpiCard
          icon={Clock}
          value={activeCount}
          label="В работе"
          hint="все незакрытые статусы"
          onClick={() => goBoard()}
          title="Открыть доску заявок"
        />
        <KpiCard
          icon={BadgeCheck}
          value={counts.closed}
          label="Закрыто"
          hint="завершены успешно"
          iconClass="bg-green-100 text-green-700 dark:bg-green-950 dark:text-green-400"
          onClick={() => goBoard('closed')}
          title="Показать закрытые на доске"
        />
        <KpiCard
          icon={ListChecks}
          value={counts.rejected}
          label="Отклонено"
          hint="дубли / нецелесообразные"
          iconClass="bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-400"
          onClick={() => goBoard('rejected')}
          title="Показать отклонённые на доске"
        />
      </div>

      <div className="mb-4 grid grid-cols-1 gap-4 lg:grid-cols-[1.55fr_1fr] lg:items-start">
        {/* Flow chart — REAL data from /api/v1/analytics/ticket-flow */}
        <div className="rounded-2xl border border-border bg-card px-[22px] py-4">
          <div className="mb-2 flex items-start justify-between">
            <div>
              <div className="flex items-center gap-2">
                <span className="text-[15px] font-semibold">Поток заявок за период</span>
              </div>
              <div className="mt-0.5 text-[12.5px] text-muted-foreground">
                Создано против закрыто по неделям.
              </div>
            </div>
            <div
              role="group"
              aria-label="Период потока заявок"
              className="flex flex-none items-center gap-0.5 rounded-[9px] border border-border p-0.5 text-[12px] font-medium"
            >
              {([12, 24, 52] as const).map((w) => (
                <button
                  key={w}
                  type="button"
                  onClick={() => setWeeks(w)}
                  aria-pressed={weeks === w}
                  className={cn(
                    'rounded-[7px] px-2.5 py-1 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                    weeks === w
                      ? 'bg-primary text-primary-foreground'
                      : 'text-muted-foreground hover:bg-muted',
                  )}
                >
                  {w}&nbsp;нед.
                </button>
              ))}
            </div>
          </div>

          {flowQ.isLoading ? (
            <div className="mt-4 flex min-h-[120px] animate-pulse items-center justify-center rounded-xl bg-muted/30">
              <div className="h-3 w-24 rounded bg-muted" />
            </div>
          ) : flowQ.isError ? (
            <div className="mt-4 flex min-h-[120px] flex-col items-center justify-center gap-2 rounded-xl bg-muted/30 text-center">
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
        <div className="rounded-2xl border border-border bg-card px-[22px] py-4">
          <div className="text-[15px] font-semibold">Распределение по статусам</div>
          <div className="mb-1.5 mt-0.5 text-[12.5px] text-muted-foreground">
            Живой срез портфеля заявок.
          </div>
          <div className="flex items-center gap-[18px]">
            <div className="relative flex-none">
              <svg viewBox="0 0 140 140" className="h-[112px] w-[112px]">
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
                <div className="text-[19px] font-bold leading-none">{total}</div>
                <div className="text-[10px] text-muted-foreground">заявок</div>
              </div>
            </div>
            <div className="flex flex-1 flex-col gap-[7px]">
              {total === 0 ? (
                <p className="text-[12px] text-muted-foreground">Нет заявок</p>
              ) : (
                presentWithRejected.map((k) => (
                  <button
                    key={k}
                    type="button"
                    onClick={() => goBoard(k)}
                    title={`Показать «${STATUS_META[k].label}» на доске`}
                    className="flex items-center gap-2 rounded-md px-1 py-0.5 text-left text-xs transition-colors hover:bg-muted/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <span
                      className="h-[9px] w-[9px] flex-none rounded-[3px]"
                      style={{ background: STATUS_META[k].color }}
                    />
                    <span className="flex-1 text-muted-foreground">{STATUS_META[k].label}</span>
                    <span className="font-semibold tabular-nums">{counts[k]}</span>
                  </button>
                ))
              )}
            </div>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1.4fr_1fr] lg:items-start">
        {/* Lifecycle funnel — REAL data */}
        <div className="rounded-2xl border border-border bg-card px-[22px] py-4">
          <div className="mb-0.5 text-[15px] font-semibold">Воронка жизненного цикла</div>
          <div className="mb-4 text-[12.5px] text-muted-foreground">
            Сколько заявок на каждом статусе прямо сейчас.
          </div>
          <div className="flex flex-col gap-2">
            {WORKFLOW_ORDER.map((k) => (
              <button
                key={k}
                type="button"
                onClick={() => goBoard(k)}
                title={`Показать «${STATUS_META[k].label}» на доске`}
                className="flex w-full items-center gap-3 rounded-md px-1 py-0.5 transition-colors hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <div className="flex w-[110px] flex-none items-center gap-2 text-left text-[12.5px] font-medium sm:w-[150px]">
                  <span
                    className="h-[9px] w-[9px] flex-none rounded-[3px]"
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
                <div className="w-[26px] flex-none text-right text-[13px] font-semibold tabular-nums">
                  {counts[k]}
                </div>
              </button>
            ))}
            {/* Rejected shown separately as a terminal branch */}
            {counts.rejected > 0 && (
              <button
                type="button"
                onClick={() => goBoard('rejected')}
                title={`Показать «${STATUS_META.rejected.label}» на доске`}
                className="flex w-full items-center gap-3 rounded-md px-1 py-0.5 opacity-70 transition-colors hover:bg-muted/50 hover:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <div className="flex w-[110px] flex-none items-center gap-2 text-left text-[12.5px] font-medium sm:w-[150px]">
                  <span
                    className="h-[9px] w-[9px] flex-none rounded-[3px]"
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
                <div className="w-[26px] flex-none text-right text-[13px] font-semibold tabular-nums">
                  {counts.rejected}
                </div>
              </button>
            )}
          </div>
        </div>

        {/* Activity feed — REAL data from /api/v1/analytics/activity */}
        <div className="rounded-2xl border border-border bg-card px-[22px] py-4">
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

/**
 * Notifications bell — unread badge + the events addressed to me.
 *
 * Pull model (PRODUCT_MODULES §5): polls /api/v1/notifications on an interval; the
 * backend derives the feed from the audit log. The panel shows only undismissed
 * events; clicking one dismisses it and opens the ticket. «Все» switches to the
 * recent history including already-read events.
 */

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Bell } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { Popover, PopoverTrigger, PopoverContent } from '@/components/ui/popover'
import { notificationsApi, type NotificationItem } from '@/api/notifications'
import { directoryApi } from '@/api/directory'
import { actionMeta } from '@/features/tickets/actions'
import { ruDateTime, shortSubject } from '@/features/tickets/format'
import { useAuth } from '../../auth/AuthProvider'
import { cn } from '@/lib/utils'

const POLL_MS = 60_000
const FEED_LIMIT = 20

function NotificationRow({
  item,
  nameOf,
  onClick,
}: {
  item: NotificationItem
  nameOf: (subject: string) => string
  onClick: () => void
}) {
  const meta = actionMeta(item.action)
  const Icon = meta.icon
  const title = item.ticket_title ?? `#${item.ticket_id.slice(0, 8)}`

  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'flex w-full items-start gap-3 rounded-[9px] px-2.5 py-2 text-left hover:bg-muted',
        item.is_read && 'opacity-60',
      )}
    >
      <span
        className={cn(
          'mt-0.5 flex h-7 w-7 flex-none items-center justify-center rounded-full',
          meta.soft,
        )}
      >
        <Icon className="h-4 w-4" style={{ color: meta.color }} />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[13px] font-medium">{title}</span>
        <span className="block text-[12px] text-muted-foreground">
          {meta.label} · {nameOf(item.actor)}
        </span>
        <span className="block text-[11px] text-muted-foreground">
          {ruDateTime(item.occurred_at)}
        </span>
      </span>
      {!item.is_read && (
        <span className="mt-2 h-[7px] w-[7px] flex-none rounded-full bg-destructive" />
      )}
    </button>
  )
}

export function NotificationsBell() {
  const [open, setOpen] = useState(false)
  const [showAll, setShowAll] = useState(false)
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { user } = useAuth()

  const feedQ = useQuery({
    queryKey: ['notifications', showAll],
    queryFn: () => notificationsApi.feed(FEED_LIMIT, showAll),
    refetchInterval: POLL_MS,
  })

  // Same key + options as UserCombobox, so this shares one cached fetch.
  const dirQ = useQuery({
    queryKey: ['directory', 'users'],
    queryFn: () => directoryApi.users(undefined, 100),
    staleTime: 5 * 60 * 1000,
  })

  const markRead = useMutation({
    mutationFn: (entryIds: string[]) => notificationsApi.markRead(entryIds),
    // Prefix match on purpose — refreshes both the unread and the «Все» view.
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['notifications'] }),
  })

  const feed = feedQ.data?.data
  const items = feed?.items ?? []
  const unreadCount = feed?.unread_count ?? 0

  const nameOf = (subject: string): string => {
    if (subject === user.subject) return 'Вы'
    const u = dirQ.data?.data.find((x) => x.subject === subject)
    return u?.full_name ?? shortSubject(subject, user.subject)
  }

  function openTicket(item: NotificationItem) {
    if (!item.is_read) markRead.mutate([item.id])
    setOpen(false)
    navigate(`/tickets/${item.ticket_id}`)
  }

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        setOpen(next)
        if (!next) setShowAll(false)
      }}
    >
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label={
            unreadCount > 0 ? `Уведомления, непрочитанных: ${unreadCount}` : 'Уведомления'
          }
          className="relative hidden h-10 w-10 items-center justify-center rounded-[11px] border border-border bg-card hover:bg-muted md:flex"
        >
          <Bell className="h-5 w-5" />
          {unreadCount > 0 && (
            <span className="absolute -right-1 -top-1 flex h-[17px] min-w-[17px] items-center justify-center rounded-full bg-destructive px-1 text-[10px] font-bold leading-none text-destructive-foreground">
              {unreadCount > 99 ? '99+' : unreadCount}
            </span>
          )}
        </button>
      </PopoverTrigger>

      <PopoverContent align="end" className="w-[340px] p-2">
        <div className="flex items-center justify-between px-2.5 pb-2 pt-1">
          <span className="text-[13px] font-semibold">
            {showAll ? 'Все уведомления' : 'Новые уведомления'}
          </span>
          <button
            type="button"
            onClick={() => setShowAll((v) => !v)}
            className="text-[12px] font-medium text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
          >
            {showAll ? 'Только новые' : 'Все'}
          </button>
        </div>

        {feedQ.isError ? (
          <p className="px-2.5 py-6 text-center text-[12.5px] text-muted-foreground">
            Не удалось загрузить уведомления.
          </p>
        ) : items.length === 0 ? (
          <p className="px-2.5 py-6 text-center text-[12.5px] text-muted-foreground">
            {feedQ.isLoading
              ? 'Загрузка…'
              : showAll
                ? 'По вашим заявкам пока нет событий.'
                : 'Новых событий по вашим заявкам нет.'}
          </p>
        ) : (
          <>
            <div className="max-h-[380px] space-y-0.5 overflow-y-auto">
              {items.map((item) => (
                <NotificationRow
                  key={item.id}
                  item={item}
                  nameOf={nameOf}
                  onClick={() => openTicket(item)}
                />
              ))}
            </div>
            {/* The «Все» view is not paged — say so rather than truncating silently. */}
            {showAll && items.length === FEED_LIMIT && (
              <p className="px-2.5 pb-1 pt-2 text-center text-[11px] text-muted-foreground">
                Показаны последние {FEED_LIMIT} событий
              </p>
            )}
          </>
        )}
      </PopoverContent>
    </Popover>
  )
}

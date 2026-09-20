/**
 * Notifications API (/api/v1/notifications).
 *
 * Pull model: the feed is derived server-side from the audit log filtered to
 * tickets the caller is involved in — there is no stored inbox. The only
 * persisted state is which events the caller has dismissed.
 */

import { api } from './client'
import type { Envelope } from './tickets'

export interface NotificationItem {
  id: string
  ticket_id: string
  ticket_title: string | null
  action: string
  actor: string
  occurred_at: string
  is_read: boolean
}

export interface NotificationFeed {
  items: NotificationItem[]
  // Always the true total unread — not the length of `items`, which is capped
  // by `limit` and excludes dismissed events unless includeRead is set.
  unread_count: number
}

export const notificationsApi = {
  feed: (limit = 20, includeRead = false) =>
    api.get<Envelope<NotificationFeed>>(
      `/api/v1/notifications?limit=${limit}&include_read=${includeRead}`,
    ),
  // Per item, not a watermark: dismissing one event leaves older unread ones
  // unread. Idempotent server-side, so a retry is harmless.
  markRead: (entryIds: string[]) =>
    api.post<Envelope<{ marked: number }>>('/api/v1/notifications/read', {
      entry_ids: entryIds,
    }),
}

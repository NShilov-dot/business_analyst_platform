import { api } from './client'
import type { Envelope } from './tickets'

export interface FlowPoint {
  week_start: string
  created: number
  closed: number
}

export interface TicketFlow {
  weeks: number
  points: FlowPoint[]
}

export interface ActivityItem {
  entry_id: string
  entity_id: string
  ticket_title: string | null
  action: string
  status: string | null
  actor: string
  occurred_at: string
}

export const analyticsApi = {
  ticketFlow: (weeks = 24) =>
    api.get<Envelope<TicketFlow>>(`/api/v1/analytics/ticket-flow?weeks=${weeks}`),
  activity: (limit = 8) =>
    api.get<Envelope<ActivityItem[]>>(`/api/v1/analytics/activity?limit=${limit}`),
}

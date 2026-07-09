/**
 * AI-intake chat API (/v1/intake-chat).
 *
 * Контекст сессии живёт на бэкенде (Postgres, per-tenant): фронт всегда может
 * восстановить диалог по GET /sessions/{id}. Финализация — человеческое
 * действие: создаёт тикет и отправляет его на триаж от имени заявителя.
 */

import { api } from './client'

export type ChatSessionStatus = 'active' | 'submitted' | 'discarded'
export type ChatRole = 'user' | 'assistant'

export interface ChatSession {
  id: string
  template_version_id: string
  status: ChatSessionStatus
  draft: Record<string, unknown>
  draft_title: string | null
  message_count: number
  ticket_id: string | null
  created_at: string
  updated_at: string
}

export interface ChatMessage {
  id: string
  seq: number
  role: ChatRole
  content: string
  created_at: string
}

export interface FieldState {
  key: string
  label: string
  required: boolean
  value: string | null
  missing: boolean
}

export interface SessionDetail {
  session: ChatSession
  messages: ChatMessage[]
  fields: FieldState[]
  is_ready: boolean
}

export interface Turn {
  session: ChatSession
  reply: ChatMessage
  fields: FieldState[]
  is_ready: boolean
}

interface Envelope<T> {
  data: T
}

export const intakeChatApi = {
  startSession: (templateVersionId?: string) =>
    api.post<Envelope<SessionDetail>>(
      '/v1/intake-chat/sessions',
      templateVersionId ? { template_version_id: templateVersionId } : {},
    ),

  getSession: (id: string) =>
    api.get<Envelope<SessionDetail>>(`/v1/intake-chat/sessions/${id}`),

  sendMessage: (id: string, content: string) =>
    api.post<Envelope<Turn>>(`/v1/intake-chat/sessions/${id}/messages`, { content }),

  finalize: (id: string) =>
    api.post<Envelope<SessionDetail>>(`/v1/intake-chat/sessions/${id}/finalize`),

  discard: (id: string) =>
    api.post<Envelope<ChatSession>>(`/v1/intake-chat/sessions/${id}/discard`),
}

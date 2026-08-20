/**
 * Documents API (/v1/documents) — per-tenant document library.
 *
 * Files uploaded here can be attached to an AI-intake chat session
 * (see intakeChat.ts `startSession(templateVersionId, documentIds)`); the
 * backend runs text extraction in-band on upload, so `status` is already
 * terminal (`extracted`/`failed`) by the time the upload response returns.
 */

import { api, postForm } from './client'

export type DocumentStatus = 'uploaded' | 'extracted' | 'failed'

export interface TenantDocument {
  id: string
  filename: string
  content_type: string
  size_bytes: number
  status: DocumentStatus
  extraction_error: string | null
  created_at: string
}

interface Envelope<T> {
  data: T
}

interface PagedEnvelope<T> {
  data: T[]
  meta: { total: number; limit: number; offset: number }
}

export interface ListDocumentsParams {
  mine?: boolean
  limit?: number
  offset?: number
}

export const documentsApi = {
  list(params: ListDocumentsParams = {}) {
    const qs = new URLSearchParams()
    if (params.mine) qs.set('mine', 'true')
    if (params.limit !== undefined) qs.set('limit', String(params.limit))
    if (params.offset !== undefined) qs.set('offset', String(params.offset))
    const q = qs.toString() ? `?${qs}` : ''
    return api.get<PagedEnvelope<TenantDocument>>(`/v1/documents${q}`)
  },

  upload(file: File) {
    const form = new FormData()
    form.append('file', file)
    return postForm<Envelope<TenantDocument>>('/v1/documents', form)
  },

  remove: (id: string) => api.delete(`/v1/documents/${id}`),
}

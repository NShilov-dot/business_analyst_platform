/** Intake-templates API (/api/v1/templates). Any tenant member may list/read. */

import { api } from './client'
import type { Envelope, PagedEnvelope } from './tickets'
import type { TemplateType } from '../features/tickets/model'

export type TemplateVersionStatus = 'draft' | 'published' | 'archived'
export type FieldKind = 'text' | 'textarea' | 'number' | 'date' | 'select' | 'multiselect'

export interface FieldDefinition {
  key: string
  label: string
  kind: FieldKind
  required: boolean
  config: Record<string, unknown>
}

export interface TemplateVersion {
  id: string
  template_id: string
  version_number: number
  status: TemplateVersionStatus
  fields: FieldDefinition[]
  created_by: string
  created_at: string
  updated_at: string
}

export interface Template {
  id: string
  type: TemplateType
  name: string
  description: string | null
  is_system: boolean
  created_at: string
  updated_at: string
}

export interface TemplateDetail extends Template {
  versions: TemplateVersion[]
}

export const templatesApi = {
  list: (limit = 100) => api.get<PagedEnvelope<Template>>(`/api/v1/templates?limit=${limit}`),
  get: (id: string) => api.get<Envelope<TemplateDetail>>(`/api/v1/templates/${id}`),
}

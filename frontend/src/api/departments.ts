/** Departments API (/v1/departments). Any tenant member may list/read. */

import { api } from './client'
import type { Envelope, PagedEnvelope } from './tickets'

export interface Department {
  id: string
  name: string
  description: string | null
  is_active: boolean
  created_at: string
  updated_at: string
}

export const departmentsApi = {
  list: (limit = 100) => api.get<PagedEnvelope<Department>>(`/v1/departments?limit=${limit}`),
  get: (id: string) => api.get<Envelope<Department>>(`/v1/departments/${id}`),
}

import { api } from './client'
import type { Envelope, PagedEnvelope, Task, TaskPriority, TaskStatus } from '../types/api'

export interface ListTasksParams {
  status?: TaskStatus
  limit?: number
  offset?: number
  /** Only the current user's own tasks (default: all tasks in the organization). */
  mine?: boolean
}

export interface CreateTaskInput {
  title: string
  description?: string
  priority?: TaskPriority
  due_at?: string
}

export interface UpdateTaskInput {
  title?: string
  description?: string | null
  priority?: TaskPriority
  due_at?: string | null
}

export const tasksApi = {
  list(params: ListTasksParams = {}) {
    const qs = new URLSearchParams()
    if (params.status)              qs.set('status', params.status)
    if (params.limit !== undefined) qs.set('limit',  String(params.limit))
    if (params.offset !== undefined) qs.set('offset', String(params.offset))
    if (params.mine)                qs.set('mine',   'true')
    const q = qs.toString() ? `?${qs}` : ''
    return api.get<PagedEnvelope<Task>>(`/v1/tasks${q}`)
  },

  get:      (id: string)                         => api.get<Envelope<Task>>(`/v1/tasks/${id}`),
  create:   (input: CreateTaskInput)             => api.post<Envelope<Task>>('/v1/tasks', input),
  update:   (id: string, input: UpdateTaskInput) => api.patch<Envelope<Task>>(`/v1/tasks/${id}`, input),
  start:    (id: string)                         => api.post<Envelope<Task>>(`/v1/tasks/${id}/start`),
  complete: (id: string)                         => api.post<Envelope<Task>>(`/v1/tasks/${id}/complete`),
  cancel:   (id: string)                         => api.post<Envelope<Task>>(`/v1/tasks/${id}/cancel`),
  delete:   (id: string)                         => api.delete(`/v1/tasks/${id}`),
}

export type TaskStatus = 'open' | 'in_progress' | 'done' | 'cancelled'
export type TaskPriority = 'low' | 'medium' | 'high'

export interface Task {
  id: string
  owner_id: string
  title: string
  description: string | null
  status: TaskStatus
  priority: TaskPriority
  due_at: string | null
  created_at: string
  updated_at: string
  completed_at: string | null
}

export interface PageMeta {
  total: number
  limit: number
  offset: number
}

export interface Envelope<T> {
  data: T
}

export interface PagedEnvelope<T> {
  data: T[]
  meta: PageMeta
}

/** Directory API (/v1/directory) — tenant user lookup backed by Keycloak. */

import { api } from './client'
import type { Envelope } from './tickets'

export interface DirectoryUser {
  subject: string
  username: string
  first_name: string | null
  last_name: string | null
  full_name: string
  email: string | null
}

export const directoryApi = {
  users: (q?: string, limit = 100) => {
    const qs = new URLSearchParams()
    if (q) qs.set('q', q)
    qs.set('limit', String(limit))
    return api.get<Envelope<DirectoryUser[]>>(`/v1/directory/users?${qs}`)
  },
}

import { api } from './client'

export interface Me {
  subject: string
  tenant_id: string
  roles: string[]
  username: string | null
  email: string | null
  given_name: string | null
  family_name: string | null
  name: string | null
}

export interface LogoutResult {
  status: string
  // Keycloak RP-Initiated Logout (end_session) URL — present when there was a
  // live session; the SPA navigates to it to also clear the browser SSO session.
  logout_url?: string | null
}

export const authApi = {
  me:     ()                 => api.get<Me>('/api/v1/auth/me'),
  logout: ()                 => api.post<LogoutResult>('/api/v1/auth/logout'),
}

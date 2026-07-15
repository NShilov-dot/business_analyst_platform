import { api } from './client'

export interface SignupInput {
  company_name: string
  slug: string
  admin_email: string
  admin_password: string
}

export interface SignupResult {
  tenant_id: string
  slug: string
  // Backend-provided URL to start the OIDC login right after signing up.
  login_url: string
}

export const signupApi = {
  register: (input: SignupInput) => api.post<SignupResult>('/v1/signup', input),
}

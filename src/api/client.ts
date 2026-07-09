/**
 * Backend-for-Frontend client.
 *
 * Auth model: HttpOnly session cookie set by the backend (BFF pattern).
 * The browser never holds an access_token — `credentials: 'include'` makes
 * fetch send the session cookie automatically on every API call.
 *
 * Single-origin: the SPA and the backend are served from the same origin (the
 * edge — nginx in docker, the Vite dev proxy locally), so the API is reached at
 * the backend's own paths (/v1/...) with no prefix rewrite. That keeps the
 * session cookie same-origin and the path-scoped oidc_state cookie aligned.
 *
 * On 401 we bounce to /v1/auth/login so the backend can start an OIDC
 * round-trip with Keycloak. The user lands back on the page they came from.
 */

import { redirectToLogin } from '../auth/AuthProvider'

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly body: unknown,
  ) {
    super(`API error ${status}`)
    this.name = 'ApiError'
  }
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
): Promise<T> {
  const res = await fetch(path, {
    method,
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })

  if (res.status === 401) {
    // Session is dead — kick the user back through the OIDC flow.
    // /auth/me is the one exception (the AuthProvider handles that case itself).
    if (!path.startsWith('/v1/auth/me')) {
      redirectToLogin()
    }
    throw new ApiError(401, null)
  }

  if (!res.ok) {
    const errBody = await res.json().catch(() => null)
    throw new ApiError(res.status, errBody)
  }

  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

export const api = {
  get:    <T>(path: string)              => request<T>('GET',    path),
  post:   <T>(path: string, body?: unknown) => request<T>('POST',   path, body),
  patch:  <T>(path: string, body?: unknown) => request<T>('PATCH',  path, body),
  delete: (path: string)                 => request<void>('DELETE', path),
}

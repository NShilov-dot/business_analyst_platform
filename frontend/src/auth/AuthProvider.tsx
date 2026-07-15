import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  ReactNode,
} from 'react'
import { authApi, Me } from '../api/auth'
import { ApiError } from '../api/client'

interface AuthContextValue {
  user: Me
  logout: () => void
}

const AuthCtx = createContext<AuthContextValue | null>(null)

// Loop-breaker: a session that keeps coming back 401 (Redis eviction between
// callback and /me, JWKS rotation invalidating a freshly-issued token, etc.)
// would otherwise ping-pong /login → Keycloak → /callback → /me 401 → /login …
// forever, hammering Keycloak. We cap redirects within a short window.
const _REDIRECT_LOG_KEY = 'auth:redirects'
const _REDIRECT_WINDOW_MS = 10_000
const _REDIRECT_MAX = 3

function _recentRedirects(now: number): number[] {
  try {
    const raw = sessionStorage.getItem(_REDIRECT_LOG_KEY)
    const arr: number[] = raw ? JSON.parse(raw) : []
    return arr.filter((t) => now - t < _REDIRECT_WINDOW_MS)
  } catch {
    return []
  }
}

/** Clear the redirect counter once a session is successfully established. */
function clearRedirectLoopState(): void {
  try {
    sessionStorage.removeItem(_REDIRECT_LOG_KEY)
  } catch {
    /* sessionStorage unavailable — nothing to clear */
  }
}

/**
 * Send the browser to /api/auth/login, preserving where we are.
 * Returns false (without navigating) if we appear to be in a redirect loop.
 */
function redirectToLogin(): boolean {
  const now = Date.now()
  const recent = _recentRedirects(now)
  if (recent.length >= _REDIRECT_MAX) {
    return false
  }
  try {
    sessionStorage.setItem(_REDIRECT_LOG_KEY, JSON.stringify([...recent, now]))
  } catch {
    /* sessionStorage unavailable — proceed without loop tracking */
  }
  const returnTo = window.location.pathname + window.location.search
  window.location.assign(`/v1/auth/login?return_to=${encodeURIComponent(returnTo)}`)
  return true
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser]       = useState<Me | null>(null)
  const [error, setError]     = useState<string | null>(null)
  const initialized           = useRef(false)

  useEffect(() => {
    // Guard against React StrictMode double-invoke
    if (initialized.current) return
    initialized.current = true

    authApi.me()
      .then((me) => {
        clearRedirectLoopState()
        setUser(me)
      })
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.status === 401) {
          // No session — bounce to login. The backend will redirect us back,
          // unless we're stuck in a redirect loop, in which case show an error
          // instead of hammering Keycloak.
          if (!redirectToLogin()) {
            setError(
              'Не удалось установить сессию (повторяющийся редирект). ' +
                'Попробуйте позже или войдите заново.',
            )
          }
          return
        }
        if (import.meta.env.DEV) console.error('[auth] /auth/me failed', err)
        setError('Could not contact the API. Please try again later.')
      })
  }, [])

  if (error) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <div className="text-center space-y-3 max-w-sm px-4">
          <p className="text-foreground font-medium">Service unavailable</p>
          <p className="text-sm text-muted-foreground">{error}</p>
          <button
            onClick={() => {
              clearRedirectLoopState()
              window.location.assign('/')
            }}
            className="px-4 py-2 text-sm border border-input rounded-md hover:bg-accent hover:text-accent-foreground transition-colors"
          >
            Retry
          </button>
        </div>
      </div>
    )
  }

  if (!user) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <div className="w-8 h-8 border-4 border-muted border-t-primary rounded-full animate-spin" />
      </div>
    )
  }

  const logout = () => {
    void authApi.logout()
      .then((res) => {
        clearRedirectLoopState()
        // RP-Initiated Logout: if the backend returns a Keycloak end_session URL,
        // follow it so the browser's SSO session is also cleared (otherwise a
        // later login silently re-authenticates on shared devices).
        window.location.assign(res?.logout_url || '/')
      })
      .catch(() => {
        window.location.assign('/')
      })
  }

  return (
    <AuthCtx.Provider value={{ user, logout }}>
      {children}
    </AuthCtx.Provider>
  )
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthCtx)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}

export { redirectToLogin }

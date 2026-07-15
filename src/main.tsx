import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider, MutationCache, QueryCache } from '@tanstack/react-query'
import { BrowserRouter } from 'react-router-dom'
import { ApiError } from './api/client'
import { getErrorMessage } from './lib/errors'
import { toast } from 'sonner'
import { ThemeProvider } from './components/theme-provider'
import App from './App'
import './index.css'

function isUnauthorized(err: unknown) {
  return err instanceof ApiError && err.status === 401
}

const queryClient = new QueryClient({
  queryCache: new QueryCache({
    onError: (err) => {
      // On 401 the api client already bounces through the OIDC login flow
      // (with a loop-breaker). Nothing to do here beyond optional dev logging.
      if (isUnauthorized(err) && import.meta.env.DEV) {
        console.warn('[query] 401 — redirecting to login')
      }
    },
  }),
  mutationCache: new MutationCache({
    onError: (err) => {
      // 401 is handled by the api client (OIDC redirect) — don't toast it.
      if (isUnauthorized(err)) {
        if (import.meta.env.DEV) console.warn('[mutation] 401 — redirecting to login')
        return
      }
      toast.error(getErrorMessage(err))
    },
  }),
  defaultOptions: {
    queries: {
      retry: (failureCount, err) => {
        // Never retry on 4xx client errors
        if (err instanceof ApiError && err.status < 500) return false
        return failureCount < 1
      },
      staleTime: 30_000,
    },
    mutations: {
      retry: false,
    },
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ThemeProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>
      <BrowserRouter>
        {/* QueryClient wraps the whole tree so both the public signup page and the
            authenticated app (gated by AuthProvider inside App) can use queries. */}
        <QueryClientProvider client={queryClient}>
          <App />
        </QueryClientProvider>
      </BrowserRouter>
    </ThemeProvider>
  </StrictMode>,
)

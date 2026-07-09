import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      // Single-origin: proxy the backend at its own paths (no rewrite), so the
      // browser uses http://localhost:5173 for both the SPA and the API. This
      // keeps the BFF session cookie same-origin and the path-scoped oidc_state
      // cookie aligned with /v1/auth/callback.
      '/v1': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})

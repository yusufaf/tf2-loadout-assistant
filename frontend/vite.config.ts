import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // Same-origin in dev, matching prod (where the API serves the built frontend
    // itself): the Steam sign-in session cookie is SameSite=Lax, which a cross-site
    // XHR from :5173 to :8000 would never carry back.
    proxy: {
      '/auth': 'http://127.0.0.1:8000',
      '/cosmetics': 'http://127.0.0.1:8000',
      '/equip-conflicts': 'http://127.0.0.1:8000',
      '/lore': 'http://127.0.0.1:8000',
      '/chat': 'http://127.0.0.1:8000',
      '/loadout': 'http://127.0.0.1:8000',
      '/healthz': 'http://127.0.0.1:8000',
      '/me': 'http://127.0.0.1:8000',
    },
  },
})

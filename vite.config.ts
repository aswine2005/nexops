import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const apiOrigin = process.env.NEXOPS_API_ORIGIN || 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': apiOrigin,
      '/health': apiOrigin,
      '/ready': apiOrigin,
      '/docs': apiOrigin,
      '/openapi.json': apiOrigin,
    },
  },
})

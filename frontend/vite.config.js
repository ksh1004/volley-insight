import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// In Docker the API is http://django-api:8000; when running `npm run dev` on the host it is localhost.
const apiTarget = process.env.VITE_API_PROXY || 'http://localhost:8000'
// Live streams (Server-Sent Events) are served by the ASGI gateway (compose service django-sse).
const sseTarget = process.env.VITE_SSE_PROXY || 'http://localhost:8001'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    port: 5173,
    // Docker Desktop on Windows does not forward file change events into bind mounts,
    // so inside the container Vite has to poll or it keeps serving old code.
    watch: process.env.VITE_USE_POLLING === 'true' ? { usePolling: true, interval: 300 } : undefined,
    proxy: {
      // listed first: Vite uses the first rule that matches
      '^/api/live/[^/]+/stream$': { target: sseTarget, changeOrigin: true },
      '/api': { target: apiTarget, changeOrigin: true },
    },
  },
})

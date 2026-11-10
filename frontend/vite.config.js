// Vite(개발 서버·빌드 도구) 설정: Vue 플러그인, '@' = src 폴더 별칭, 개발 서버 포트와 API 프록시.
import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Docker 안에서는 API가 http://django-api:8000, PC에서 직접 `npm run dev`로 띄우면 localhost다.
const apiTarget = process.env.VITE_API_PROXY || 'http://localhost:8000'
// 실시간 스트림(SSE)은 ASGI 게이트웨이(compose 서비스 django-sse)가 처리한다.
const sseTarget = process.env.VITE_SSE_PROXY || 'http://localhost:8001'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    port: 5173,
    // Windows의 Docker Desktop은 바인드 마운트 폴더의 파일 변경 알림을 컨테이너에 전달하지 않는다.
    // 그래서 컨테이너 안의 Vite는 주기적으로 파일을 확인(polling)해야 한다. 아니면 옛 코드를 계속 보여 준다.
    watch: process.env.VITE_USE_POLLING === 'true' ? { usePolling: true, interval: 300 } : undefined,
    proxy: {
      // 먼저 적는다: Vite는 처음 맞는 규칙을 쓰므로, 스트림 주소가 일반 /api 규칙보다 앞에 와야 한다
      '^/api/live/[^/]+/stream$': { target: sseTarget, changeOrigin: true },
      '/api': { target: apiTarget, changeOrigin: true },
    },
  },
})

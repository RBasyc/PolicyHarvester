import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  // SPA 回退（dev 下刷新 /library/123 不 404）；prod 用 hash 模式，本就无需回退
  appType: 'spa',
  plugins: [vue()],
  server: {
    proxy: {
      // 后端 FastAPI（server/main.py，默认 8000）；SSE 需要关掉代理缓冲
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
